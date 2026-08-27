from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from .beta_v2 import attach_beta_v2_state, fetch_beta_v2_state
from .broker_reconcile import BrokerReconciler
from .context import build_market_context
from .events import EventState, event_state_at
from .features import compute_features
from .hardening import (
    HardenedEngineService,
    _input_health,
    _on_entry_grid,
    _parse_iso,
    _state_from_trust,
)
from .prediction import create_prediction_bundle
from .regime import estimate_dealer_gamma_proxy, estimate_option_activity_proxy
from .risk import choose_decision, no_trade_decision
from .services import append_jsonl
from .strategy_v2 import generate_candidates_v2
from .timeutil import ET, et_now
from .v2_service import apply_preview_fees, record_chain_tape


class V2HardenedEngineService(HardenedEngineService):
    """Production-hardening path with V2 forecasting handoff and payoff search.

    Every existing fail-closed integrity, surface, uncertainty, broker,
    reconciliation, entry-grid and risk control remains authoritative. V2 changes
    only the information handoff and the candidate-search/economic ranking layer.
    """

    def __init__(self, *args: Any, beta_state_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.beta_state_url = (
            beta_state_url
            or os.environ.get("BETA_SPY_STATE_URL")
            or "http://127.0.0.1:8790/api/state"
        ).strip()

    def run_once(self) -> str | None:
        self._process_commands()
        snapshot = self.journal.latest_snapshot()
        if not snapshot:
            return None
        last = self.journal.get_control("last_engine_snapshot_id")
        if last == snapshot["snapshot_id"]:
            return None
        ready = self.journal.get_control("market_ready_snapshot_id")
        if snapshot.get("source") == "tradier_production_stream" and ready != snapshot["snapshot_id"]:
            return None

        chain, options = self.journal.latest_option_chain("SPY")
        if chain:
            try:
                chain_age = abs(
                    (_parse_iso(snapshot["captured_at"]) - _parse_iso(chain["captured_at"])).total_seconds()
                )
            except Exception:
                chain_age = float("inf")
            if chain_age > self.config.market.option_quote_stale_seconds:
                options = []

        quotes = self.journal.snapshot_quotes(snapshot["snapshot_id"])
        feature = compute_features(self.journal, self.config, snapshot, quotes)
        surface = self.journal.surface_metrics(snapshot["snapshot_id"])
        spot = float(snapshot.get("spy_price") or 0.0)
        gamma = estimate_dealer_gamma_proxy(options, spot)
        option_activity = estimate_option_activity_proxy(options, spot)
        context = build_market_context(self.journal, self.config, snapshot, quotes)
        event = event_state_at(self.config, _parse_iso(snapshot["captured_at"]))
        configured_event = self.journal.get_control("event_state", "").strip()
        if configured_event in {"macro_announcement", "rebalance", "earnings_heavy", "unknown"}:
            event = EventState(
                state=configured_event,
                source="operator_override",
                title="operator override",
                blocked=configured_event in {"macro_announcement", "rebalance", "unknown"},
            )
        input_health = _input_health(
            self.config,
            snapshot,
            chain,
            options,
            surface,
            gamma,
            context=context,
            event=event,
        )
        feature.setdefault("payload", {})["hardening"] = {
            "gamma": gamma,
            "option_activity": option_activity,
            "event_state": event.state,
            "event": event.as_dict(),
            "market_context": context.as_dict(),
            "input_health": input_health,
        }
        trust = float(feature.get("trust_score") or 0.0) * float(input_health["trust_multiplier"])
        if not input_health["required_ok"]:
            trust = 0.0
            feature["health_state"] = "RED"
        else:
            feature["health_state"] = _state_from_trust(self.config, trust)
        feature["trust_score"] = max(0.0, min(1.0, trust))
        self.journal.insert_features(feature)

        predictions = create_prediction_bundle(
            self.journal,
            self.config,
            snapshot,
            feature,
            quotes=quotes,
        )
        prediction = next(
            (
                member
                for member in predictions
                if int(member["horizon_minutes"]) == int(self.config.prediction.horizon_minutes)
            ),
            predictions[0],
        )

        beta_state = fetch_beta_v2_state(self.beta_state_url)
        created = datetime.fromisoformat(str(prediction["created_at"]).replace("Z", "+00:00"))
        if beta_state is not None and not beta_state.is_current(created):
            beta_state = None
        prediction = attach_beta_v2_state(prediction, beta_state)
        prediction.setdefault("payload", {})["architecture"] = "alpha-beta-v2-liquidity-first"
        prediction["model_version"] = "alpha-beta-v2.0.0"

        for member in predictions:
            if member["prediction_id"] == prediction["prediction_id"]:
                self.journal.insert_prediction(prediction)
            else:
                self.journal.insert_prediction(member)

        chain_hash = record_chain_tape(self.config, chain, options, prediction)
        prediction.setdefault("payload", {})["v2_chain_sha256"] = chain_hash
        candidates = generate_candidates_v2(self.config, prediction, options)
        apply_preview_fees(self.config, candidates)
        candidates.sort(
            key=lambda candidate: (
                candidate.get("status") == "ELIGIBLE",
                float(candidate.get("score") or -1e9),
            ),
            reverse=True,
        )
        self.journal.insert_candidates(candidates)
        account = self._account_state()

        reconciliation = BrokerReconciler(self.config, self.journal).check(self.journal.open_position())
        distribution = prediction.get("payload", {}).get("distribution", {})
        pq_surface_weight = float(distribution.get("q_surface_covered_weight") or 0.0)
        model_uncertainty = float(prediction.get("payload", {}).get("model_uncertainty") or 0.0)
        beta_payload = prediction.get("payload", {}).get("beta_v2", {})
        beta_available = beta_state is not None
        beta_magnitude_trust = float(beta_payload.get("magnitude_trust") or 0.0)
        beta_regime = str(beta_payload.get("regime") or "UNAVAILABLE")

        if not input_health["required_ok"]:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "input_integrity_block",
                payload={"input_health": input_health},
            )
        elif pq_surface_weight < self.config.prediction.minimum_pq_surface_weight:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "pq_surface_coverage_block",
                payload={"q_surface_covered_weight": pq_surface_weight},
            )
        elif model_uncertainty >= self.config.prediction.max_model_uncertainty:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "model_uncertainty_block",
                payload={"model_uncertainty": model_uncertainty},
            )
        elif self.config.risk.block_on_broker_reconciliation and reconciliation.blocked:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "broker_reconciliation_blocked",
                payload={"reconciliation": reconciliation.as_dict()},
            )
        elif not beta_available:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "beta_v2_unavailable",
                payload={"beta_v2": beta_payload},
            )
        elif beta_regime == "UNTRUSTED" or beta_magnitude_trust < 0.15:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "beta_v2_validation_block",
                payload={"beta_v2": beta_payload},
            )
        elif not _on_entry_grid(self.config, snapshot["captured_at"]):
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "off_entry_grid",
                payload={"entry_grid_minutes": self.config.risk.entry_grid_minutes},
            )
        else:
            local = _parse_iso(snapshot["captured_at"]).astimezone(ET)
            grid = max(1, int(self.config.risk.entry_grid_minutes))
            bucket_minute = local.minute - (local.minute % grid)
            bucket = local.replace(minute=bucket_minute, second=0, microsecond=0).isoformat()
            if self.journal.get_control("last_entry_grid_bucket") == bucket:
                decision = no_trade_decision(
                    prediction,
                    feature,
                    account,
                    "entry_grid_already_evaluated",
                    payload={"entry_grid_bucket": bucket},
                )
            else:
                self.journal.set_control("last_entry_grid_bucket", bucket)
                decision = choose_decision(
                    self.config,
                    self.journal,
                    prediction,
                    feature,
                    candidates,
                    account,
                    now=_parse_iso(snapshot["captured_at"]),
                )

        decision.setdefault("payload", {})["architecture"] = "alpha-beta-v2-liquidity-first"
        decision["payload"]["beta_v2"] = beta_payload
        decision["payload"]["chain_sha256"] = chain_hash
        self.journal.insert_decision(decision)

        if decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"} and decision.get("candidate_id"):
            candidate = next(
                (item for item in candidates if item["candidate_id"] == decision["candidate_id"]),
                None,
            )
            if candidate:
                try:
                    self.execution.execute(decision, candidate)
                except Exception as exc:
                    self.journal.alert("critical", "V2 order execution failed", str(exc), "execution")
                    self.publisher.alert("critical", "V2 order execution failed", str(exc), "execution")

        self.journal.set_control("last_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, prediction, account)
        append_jsonl(
            self.config.paths.state_root
            / "candidates"
            / f"v2-candidates-{et_now().date().isoformat()}.jsonl",
            {
                "prediction": prediction,
                "prediction_bundle": predictions,
                "feature": feature,
                "chain_sha256": chain_hash,
                "candidates": candidates,
                "decision": decision,
                "reconciliation": reconciliation.as_dict(),
            },
        )
        return f"v2 prediction={prediction['prediction_id']} action={decision['action']}"

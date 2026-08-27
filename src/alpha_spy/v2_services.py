from __future__ import annotations

import contextlib
import gzip
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import SuiteConfig
from .execution import ExecutionManager, build_multileg_payload
from .features import compute_features
from .prediction import create_prediction
from .risk import choose_decision, no_trade_decision
from .services import EngineService, MarketService
from .strategy_v2 import V2OptimizerConfig, generate_v2_candidates
from .timeutil import et_now, utc_iso
from .tradier import TradierClient, normalize_option, preview_fees


class V2MarketService(MarketService):
    """Capture the complete same-day SPY option surface for deterministic replay."""

    def _collect_spy_chain(self, client: TradierClient, snapshot_id: str, spy_price: float) -> None:
        expirations = client.expirations("SPY")
        today = et_now().date().isoformat()
        if today not in expirations:
            self.journal.alert(
                "warning",
                "No 0DTE SPY expiry",
                "Tradier has no same-session expiration; V2 refuses expiry fallback",
                "market",
            )
            return
        rows = [
            normalize_option(row)
            for row in client.option_chain("SPY", today, self.config.market.option_chain_greeks)
        ]
        options = sorted(
            [row for row in rows if row.get("symbol") and float(row.get("strike") or 0.0) > 0],
            key=lambda row: (str(row.get("right") or ""), float(row.get("strike") or 0.0)),
        )
        chain_id = f"OC-{snapshot_id}"
        captured_at = utc_iso()
        self.journal.insert_option_chain(
            {
                "chain_snapshot_id": chain_id,
                "captured_at": captured_at,
                "underlying": "SPY",
                "purpose": "strategy",
                "expiration": today,
                "underlying_price": spy_price,
                "integrity": "VERIFIED" if options else "INCOMPLETE",
                "source": "tradier",
                "payload": {"v2_full_chain": True, "row_count": len(options)},
            },
            options,
        )
        archive = (
            self.config.paths.state_root
            / "market"
            / f"spy-options-full-{et_now().date().isoformat()}.jsonl.gz"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(archive, "at", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "chain_snapshot_id": chain_id,
                        "captured_at": captured_at,
                        "expiration": today,
                        "underlying_price": spy_price,
                        "options": options,
                    },
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            )


class V2EngineService(EngineService):
    """Beta-gated, liquidity-first Alpha V2 engine."""

    def __init__(
        self,
        config: SuiteConfig,
        journal,
        *,
        beta_state_url: str | None = None,
        optimizer_config: V2OptimizerConfig | None = None,
    ) -> None:
        hardened = config.model_copy(deep=True)
        hardened.risk.maximum_contracts = 1
        hardened.risk.maximum_trades_per_day = 1
        hardened.risk.maximum_trade_risk_dollars = min(
            100.0, float(hardened.risk.maximum_trade_risk_dollars)
        )
        hardened.risk.entry_stop_time_et = "15:40"
        hardened.trading.fee_per_contract = 0.0
        super().__init__(hardened, journal)
        self.beta_state_url = (
            beta_state_url
            or os.getenv("BETA_SPY_STATE_URL")
            or "http://127.0.0.1:8000/api/state"
        )
        self.optimizer_config = optimizer_config or V2OptimizerConfig()
        self.execution = ExecutionManager(self.config, journal)

    def _beta_opportunity(self, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
        try:
            response = httpx.get(self.beta_state_url, timeout=2.5)
            response.raise_for_status()
            state = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return None, f"beta_v2_unavailable:{type(exc).__name__}"
        if not isinstance(state, dict):
            return None, "beta_v2_invalid_state"
        opportunity = state.get("v2_opportunity") or state.get("opportunity")
        if not isinstance(opportunity, dict):
            return None, "beta_v2_missing_opportunity"
        try:
            stamp = datetime.fromisoformat(str(opportunity.get("timestamp") or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        except ValueError:
            return None, "beta_v2_bad_timestamp"
        age = (now.astimezone(UTC) - stamp.astimezone(UTC)).total_seconds()
        if age < -5 or age > 120:
            return None, f"beta_v2_stale:{age:.0f}s"
        if opportunity.get("strategy_authority") not in (False, None):
            return None, "beta_v2_strategy_authority_violation"
        if float(opportunity.get("trust") or 0.0) < 0.25:
            return None, "beta_v2_trust_below_threshold"
        if opportunity.get("eligible") is not True:
            return None, "beta_v2_no_trade"
        return opportunity, None

    @staticmethod
    def _attach_beta(prediction: dict[str, Any], opportunity: dict[str, Any]) -> dict[str, Any]:
        out = deepcopy(prediction)
        payload = dict(out.get("payload") or {})
        payload["beta_v2"] = {
            **opportunity,
            "strategy_authority": False,
            "role": "opportunity_and_distribution_prior",
        }
        out["payload"] = payload
        return out

    def _preview_fee_gate(self, candidate: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        if not self.config.trading.submit_orders:
            return True, {"preview": "not_required_for_local_paper"}
        try:
            payload = build_multileg_payload(candidate, 1, float(candidate["entry_price"]))
            with TradierClient(self.config) as client:
                preview = client.preview_order(payload)
            one_way = preview_fees(preview) or 0.0
            roundtrip = 2.0 * one_way
        except Exception as exc:
            return False, {"preview_error": str(exc)}
        net_after_preview = float(candidate.get("expected_value") or 0.0) - roundtrip
        return net_after_preview > 0.0, {
            "preview_roundtrip_fee_estimate": roundtrip,
            "expected_value_after_preview_fees": net_after_preview,
        }

    def run_once(self) -> str | None:
        self._process_commands()
        snapshot = self.journal.latest_snapshot()
        if not snapshot:
            return None
        last = self.journal.get_control("last_v2_engine_snapshot_id")
        if last == snapshot["snapshot_id"]:
            return None

        quotes = self.journal.snapshot_quotes(snapshot["snapshot_id"])
        feature = compute_features(self.journal, self.config, snapshot, quotes)
        self.journal.insert_features(feature)
        prediction = create_prediction(self.journal, self.config, snapshot, feature)
        now = datetime.now(UTC)
        beta, beta_failure = self._beta_opportunity(now)
        account = self._account_state()

        if beta_failure:
            self.journal.insert_prediction(prediction)
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                beta_failure,
                now=now,
                payload={"v2": True, "beta_state_url": self.beta_state_url},
            )
            self.journal.insert_decision(decision)
            self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
            self._publish(snapshot, feature, prediction, account)
            return f"prediction={prediction['prediction_id']} action=NO_TRADE reason={beta_failure}"

        assert beta is not None
        prediction = self._attach_beta(prediction, beta)
        self.journal.insert_prediction(prediction)
        chain, options = self.journal.latest_option_chain("SPY")
        if chain and chain["captured_at"] < snapshot["captured_at"]:
            options = []

        candidates = generate_v2_candidates(
            self.config,
            prediction,
            options,
            optimizer_config=self.optimizer_config,
        )
        self.journal.insert_candidates(candidates)
        decision = choose_decision(
            self.config, self.journal, prediction, feature, candidates, account, now=now
        )

        if decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"} and decision.get("candidate_id"):
            candidate = next(
                (row for row in candidates if row["candidate_id"] == decision["candidate_id"]),
                None,
            )
            if candidate is not None:
                preview_ok, preview_detail = self._preview_fee_gate(candidate)
                candidate.setdefault("payload", {}).setdefault("v2", {}).update(preview_detail)
                if not preview_ok:
                    decision = no_trade_decision(
                        prediction,
                        feature,
                        account,
                        "broker_preview_cost_killed_edge",
                        now=now,
                        payload={"v2": True, "candidate": candidate, **preview_detail},
                    )
                else:
                    try:
                        self.execution.execute(decision, candidate)
                    except Exception as exc:
                        self.journal.alert("critical", "V2 order execution failed", str(exc), "execution")
                        with contextlib.suppress(Exception):
                            self.publisher.alert("critical", "V2 order execution failed", str(exc), "execution")

        self.journal.insert_decision(decision)
        self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, prediction, account)
        return f"prediction={prediction['prediction_id']} action={decision['action']}"

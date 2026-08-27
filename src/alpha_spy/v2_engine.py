from __future__ import annotations

import contextlib
import json
import math
import statistics
from datetime import UTC, datetime
from typing import Any

import httpx

from .context import build_market_context
from .events import event_state_at
from .execution import build_multileg_payload
from .features import compute_features
from .prediction import create_prediction
from .regime import classify_regime_hierarchy, estimate_dealer_gamma_proxy
from .risk import allowed_risk, choose_decision, no_trade_decision
from .tradier import TradierClient, preview_fees
from .v2_learning import playbook_history, refresh_closed_trade_reviews
from .v2_lifecycle import AlphaRegimeLifecycleEngine
from .v2_pending_entry import resolve_pending_entry
from .v2_services import V2EngineService as _BaseV2EngineService
from .v2_state_pq import generate_state_pq_candidates
from .v2_trader_agent import build_agent_plan

DEFAULT_BETA_V2_STATE_URL = "http://127.0.0.1:8790/api/state"
TRADER_AGENT_AUTHORITY = "alpha_v2_closed_loop_trader_agent"
MAX_SCALE_UNITS = 5


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class V2EngineService(_BaseV2EngineService):
    """Master V2 orchestrator for the closed-loop trader.

    Authority is deliberately separated:
    Alpha hierarchy -> regime definition; lifecycle -> duration/transition;
    Beta -> independent constituent/HGB witness; Alpha P/Q -> trade design;
    execution/settlement -> implementation and management; learning/governance ->
    post-trade attribution. Positive option EV alone never permits a trade.
    """

    def __init__(self, config, journal, *, beta_state_url: str | None = None, **kwargs: Any):
        super().__init__(
            config,
            journal,
            beta_state_url=beta_state_url or DEFAULT_BETA_V2_STATE_URL,
            **kwargs,
        )
        self.config.risk.maximum_trades_per_day = max(8, int(self.config.risk.maximum_trades_per_day))
        self.lifecycle = AlphaRegimeLifecycleEngine(journal)

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
            stamp = _parse_time(opportunity.get("timestamp") or "")
        except ValueError:
            return None, "beta_v2_bad_timestamp"
        age = (now.astimezone(UTC) - stamp).total_seconds()
        if age < -5 or age > 120:
            return None, f"beta_v2_stale:{age:.0f}s"
        if opportunity.get("strategy_authority") not in (False, None):
            return None, "beta_v2_strategy_authority_violation"

        state_payload = opportunity.get("predictive_state") or {}
        hgb = opportunity.get("hgb_direction") or {}
        state_ready = isinstance(state_payload, dict) and bool(state_payload.get("ready"))
        hgb_ready = isinstance(hgb, dict) and bool(hgb.get("eligible"))
        if not state_ready and not hgb_ready:
            return None, "beta_v2_models_warming"
        return opportunity, None

    @staticmethod
    def _atm_iv(options: list[dict[str, Any]], spot: float) -> float | None:
        rows = [
            float(row.get("iv") or 0.0)
            for row in options
            if float(row.get("iv") or 0.0) > 0.0
            and abs(float(row.get("strike") or 0.0) - spot) <= 1.5
        ]
        if not rows:
            rows = [float(row.get("iv") or 0.0) for row in options if float(row.get("iv") or 0.0) > 0.0]
        return float(statistics.median(rows)) if rows else None

    def _preview_fee_gate(self, candidate: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        has_execution_preview = bool(
            self.config.tradier.access_token.get_secret_value() and self.config.tradier.account_id
        )
        if not has_execution_preview:
            return True, {
                "preview": "execution_credentials_unavailable",
                "cost_source": "actual_chain_quote_drag_plus_pass_through_estimate",
            }

        try:
            payload = build_multileg_payload(candidate, 1, float(candidate["entry_price"]))
            with TradierClient(self.config) as client:
                preview = client.preview_order(payload)
            one_way = preview_fees(preview)
            if one_way is None:
                return True, {
                    "preview": preview,
                    "cost_source": "actual_chain_quote_drag_plus_pass_through_estimate",
                    "preview_fee_fields_missing": True,
                }
            roundtrip = 2.0 * one_way
        except Exception as exc:
            if self.config.trading.submit_orders:
                return False, {
                    "preview_error": str(exc),
                    "cost_source": "broker_preview_failed_closed",
                }
            return True, {
                "preview_error": str(exc),
                "cost_source": "actual_chain_quote_drag_plus_pass_through_estimate",
            }

        payload = candidate.get("payload") or {}
        thesis = payload.get("trade_thesis") or {}
        economics = thesis.get("economics") or {}
        robust_ev = float(economics.get("robust_ev_after_3x_drag_dollars") or 0.0)
        max_loss = float(candidate.get("max_loss") or 0.0)
        preview_risk = max_loss + roundtrip
        robust_after_preview = robust_ev - roundtrip
        ok = (
            roundtrip <= 5.0
            and preview_risk <= min(100.0, float(self.config.risk.maximum_trade_risk_dollars)) + 1e-9
            and robust_after_preview > 0.0
        )
        return ok, {
            "preview": preview,
            "preview_roundtrip_fee_estimate": roundtrip,
            "preview_max_loss_dollars": preview_risk,
            "robust_ev_after_preview_fees": robust_after_preview,
            "cost_source": "tradier_order_preview",
            "preview_policy": "closed_loop_trader_cost_and_risk_veto",
        }

    @staticmethod
    def _mark_candidates(
        candidates: list[dict[str, Any]],
        *,
        authorized_id: str | None,
    ) -> None:
        for candidate in candidates:
            payload = dict(candidate.get("payload") or {})
            if authorized_id and candidate.get("candidate_id") == authorized_id:
                candidate["status"] = "ELIGIBLE"
                candidate["rejection_reason"] = None
                payload["shadow_only"] = False
                payload["execution_authority"] = True
            else:
                candidate["status"] = "SHADOW"
                candidate["rejection_reason"] = "closed_loop_agent_not_current_expression"
                payload["shadow_only"] = True
                payload["execution_authority"] = False
            candidate["payload"] = payload

    def _pending_state(self, market_state: dict[str, Any], now: datetime) -> tuple[str, str, dict[str, Any] | None]:
        raw = self.journal.get_control("v2_pending_trade_thesis")
        if not raw:
            return "NONE", "no_pending_thesis", None
        try:
            thesis = json.loads(raw)
        except json.JSONDecodeError:
            self.journal.set_control("v2_pending_trade_thesis", "")
            return "CANCEL", "pending_thesis_corrupt", None
        if not isinstance(thesis, dict):
            self.journal.set_control("v2_pending_trade_thesis", "")
            return "CANCEL", "pending_thesis_invalid", None
        resolution = resolve_pending_entry(thesis, market_state, now=now)
        if resolution.action in {"RELEASE", "CANCEL"}:
            self.journal.set_control("v2_pending_trade_thesis", "")
        return resolution.action, resolution.reason, thesis

    def _current_chain(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        chain, options = self.journal.latest_option_chain("SPY")
        if not chain:
            return chain, options
        try:
            age = abs((_parse_time(snapshot["captured_at"]) - _parse_time(chain["captured_at"])).total_seconds())
        except (TypeError, ValueError):
            return chain, []
        if age > float(self.config.market.option_quote_stale_seconds):
            return chain, []
        return chain, options

    def _market_intelligence(
        self,
        *,
        snapshot: dict[str, Any],
        feature: dict[str, Any],
        quotes: list[dict[str, Any]],
        options: list[dict[str, Any]],
        beta: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        stamp = _parse_time(snapshot["captured_at"])
        context = build_market_context(self.journal, self.config, snapshot, quotes)
        event = event_state_at(self.config, stamp)
        gamma = estimate_dealer_gamma_proxy(options, float(snapshot.get("spy_price") or 0.0))
        hierarchy = classify_regime_hierarchy(
            self.journal,
            timestamp=stamp,
            feature=feature,
            gamma_state=str(gamma.get("state") or "unknown_gamma"),
            event_state=event.state,
            context=context,
        )
        lifecycle = self.lifecycle.forecast(
            snapshot_id=str(snapshot["snapshot_id"]),
            captured_at=str(snapshot["captured_at"]),
            hierarchy=hierarchy,
            beta=beta,
        )
        lifecycle_dict = lifecycle.as_dict()
        regime_forecast = {
            "definable": lifecycle.definable,
            "current_regime": lifecycle.current_regime,
            "confidence": lifecycle.confidence,
            "persistence_5": lifecycle.persistence_5,
            "persistence_15": lifecycle.persistence_15,
            "persistence_30": lifecycle.persistence_30,
            "expected_duration_minutes": lifecycle.expected_remaining_minutes,
            "expected_remaining_minutes": lifecycle.expected_remaining_minutes,
            "remaining_duration_quantiles": lifecycle.remaining_duration_quantiles,
            "hazard_0_5": lifecycle.hazard_0_5,
            "hazard_5_15": lifecycle.hazard_5_15,
            "hazard_15_30": lifecycle.hazard_15_30,
            "successor_probabilities": lifecycle.successor_probabilities,
            "most_likely_successor": lifecycle.most_likely_successor,
            "successor_confidence": lifecycle.successor_confidence,
            "source": lifecycle.source,
            "matched_episodes": lifecycle.matched_episodes,
            "effective_episodes": lifecycle.effective_episodes,
            "reasons": list(lifecycle.reasons),
        }
        market_state = dict(beta or {})
        market_state["beta_regime_forecast"] = (beta or {}).get("regime_forecast")
        market_state["regime_forecast"] = regime_forecast
        market_state["alpha_regime"] = hierarchy.as_dict()
        market_state["lifecycle"] = lifecycle_dict
        market_state["regime_authority"] = "alpha_hierarchical_regime"
        market_state["lifecycle_authority"] = "alpha_empirical_regime_lifecycle"
        self.journal.set_control(
            "v2_current_agent_market_state",
            json.dumps(
                {
                    "timestamp": stamp.isoformat(),
                    "regime_forecast": regime_forecast,
                    "alpha_regime": hierarchy.as_dict(),
                    "lifecycle": lifecycle_dict,
                },
                separators=(",", ":"),
            ),
        )
        return market_state, hierarchy.as_dict(), lifecycle_dict

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
        _, options = self._current_chain(snapshot)
        now = datetime.now(UTC)
        beta, beta_failure = self._beta_opportunity(now)
        market_state, alpha_regime, lifecycle = self._market_intelligence(
            snapshot=snapshot,
            feature=feature,
            quotes=quotes,
            options=options,
            beta=beta,
        )
        refresh_closed_trade_reviews(self.journal, limit=50)

        base_prediction = create_prediction(self.journal, self.config, snapshot, feature)
        base_payload = dict(base_prediction.get("payload") or {})
        base_payload["alpha_regime"] = alpha_regime
        base_payload["regime_authority"] = "alpha_hierarchical_regime"
        base_payload["lifecycle"] = lifecycle
        base_payload["lifecycle_authority"] = "alpha_empirical_regime_lifecycle"
        if beta is not None:
            base_payload["beta_witness"] = {
                "hgb_direction": beta.get("hgb_direction"),
                "predictive_state": beta.get("predictive_state"),
                "regime_forecast": beta.get("regime_forecast"),
                "strategy_authority": False,
            }
        base_prediction["payload"] = base_payload
        account = self._account_state()

        if beta_failure:
            self.journal.insert_prediction(base_prediction)
            decision = no_trade_decision(
                base_prediction,
                feature,
                account,
                beta_failure,
                now=now,
                payload={
                    "v2": True,
                    "beta_state_url": self.beta_state_url,
                    "alpha_regime": alpha_regime,
                    "lifecycle": lifecycle,
                },
            )
            self.journal.insert_decision(decision)
            self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
            self._publish(snapshot, feature, base_prediction, account)
            return f"prediction={base_prediction['prediction_id']} action=NO_TRADE reason={beta_failure}"

        assert beta is not None
        beta_prediction = self._attach_beta(base_prediction, beta)
        state_prediction, full_candidates = generate_state_pq_candidates(
            self.config,
            beta_prediction,
            beta,
            options,
            optimizer_config=self.optimizer_config,
        )
        state_payload = dict(state_prediction.get("payload") or {})
        state_payload["alpha_regime"] = alpha_regime
        state_payload["lifecycle"] = lifecycle
        state_payload["regime_authority"] = "alpha_hierarchical_regime"
        state_payload["lifecycle_authority"] = "alpha_empirical_regime_lifecycle"
        state_prediction["payload"] = state_payload
        self.journal.insert_prediction(state_prediction)

        pending_action, pending_reason, pending_thesis = self._pending_state(market_state, now)
        history = playbook_history(self.journal)
        if pending_action == "WAIT":
            plan = None
            plan_action = "WAIT"
            plan_reason = pending_reason
            plan_playbook = str((pending_thesis or {}).get("playbook") or "PENDING_SETUP")
            plan_diagnostics = {
                "pending": True,
                "pending_reason": pending_reason,
                "pending_thesis_id": (pending_thesis or {}).get("thesis_id"),
            }
            thesis_dict = pending_thesis
            chosen = None
            setup_key = str((pending_thesis or {}).get("setup_key") or "")
        else:
            plan = build_agent_plan(
                market_state,
                full_candidates,
                now=now,
                playbook_history=history,
            )
            plan_action = plan.action
            plan_reason = plan.reason
            plan_playbook = plan.playbook
            plan_diagnostics = plan.diagnostics
            chosen = plan.candidate if plan.action == "ENTER" else None
            setup_key = plan.thesis.setup_key if plan.thesis is not None else ""
            thesis_dict = plan.thesis.as_dict() if plan.thesis is not None else None

            if thesis_dict is not None:
                thesis_dict["alpha_regime"] = alpha_regime
                thesis_dict["lifecycle"] = lifecycle
                thesis_dict["lifecycle_forecast_id"] = lifecycle.get("forecast_id")
                thesis_dict["beta_witness"] = lifecycle.get("beta_witness")
                setup_market = {
                    "spot": float(snapshot.get("spy_price") or 0.0),
                    "atm_iv": self._atm_iv(options, float(snapshot.get("spy_price") or 0.0)),
                    "captured_at": str(snapshot.get("captured_at") or ""),
                    "beta_timestamp": beta.get("timestamp"),
                }
                thesis_dict["market_at_setup"] = setup_market
                if plan.candidate is not None:
                    per_unit_risk = max(float(plan.candidate.get("max_loss") or 0.0), 1e-9)
                    risk_budget = allowed_risk(
                        self.config,
                        account,
                        float(feature.get("trust_score") or 0.0),
                        str(feature.get("health_state") or "RED"),
                    )
                    quantity_capacity = max(1, int(math.floor(risk_budget / per_unit_risk)))
                    thesis_dict["risk_budget_dollars"] = risk_budget
                    thesis_dict["per_unit_max_loss_dollars"] = per_unit_risk
                    thesis_dict["maximum_quantity"] = min(MAX_SCALE_UNITS, quantity_capacity)
                    if chosen is not None:
                        thesis_dict["market_at_entry"] = setup_market

                    candidate_payload = dict(plan.candidate.get("payload") or {})
                    candidate_payload["authority"] = TRADER_AGENT_AUTHORITY
                    candidate_payload["trade_thesis"] = thesis_dict
                    candidate_payload["trader_agent"] = {
                        "plan_action": plan.action,
                        "plan_reason": plan.reason,
                        "diagnostics": plan.diagnostics,
                        "playbook_governance": history.get(plan.playbook, {}),
                    }
                    plan.candidate["payload"] = candidate_payload

        already_traded = bool(setup_key and self.journal.get_control("last_v2_agent_setup_key") == setup_key)
        if already_traded and chosen is not None:
            chosen = None
            plan_reason = "setup_episode_already_traded"

        authorized_id = chosen.get("candidate_id") if chosen is not None else None
        self._mark_candidates(full_candidates, authorized_id=authorized_id)
        self.journal.insert_candidates(full_candidates)

        if plan_action == "WAIT" and thesis_dict is not None and pending_action != "WAIT":
            self.journal.set_control(
                "v2_pending_trade_thesis",
                json.dumps(thesis_dict, separators=(",", ":")),
            )
        elif plan_action == "NO_TRADE":
            self.journal.set_control("v2_pending_trade_thesis", "")

        if chosen is None:
            decision = no_trade_decision(
                state_prediction,
                feature,
                account,
                plan_reason,
                now=now,
                payload={
                    "v2": True,
                    "trader_agent": {
                        "action": plan_action,
                        "playbook": plan_playbook,
                        "diagnostics": plan_diagnostics,
                        "trade_thesis": thesis_dict,
                        "pending_resolution": pending_action,
                        "alpha_regime": alpha_regime,
                        "lifecycle": lifecycle,
                    },
                },
            )
        else:
            decision = choose_decision(
                self.config,
                self.journal,
                state_prediction,
                feature,
                [chosen],
                account,
                now=now,
            )
            decision.setdefault("payload", {})["trader_agent"] = {
                "playbook": plan_playbook,
                "trade_thesis": thesis_dict,
                "diagnostics": plan_diagnostics,
                "pending_resolution": pending_action,
                "alpha_regime": alpha_regime,
                "lifecycle": lifecycle,
            }

        if decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"} and chosen is not None:
            preview_ok, preview_detail = self._preview_fee_gate(chosen)
            chosen.setdefault("payload", {}).setdefault("execution_preview", {}).update(preview_detail)
            if not preview_ok:
                decision = no_trade_decision(
                    state_prediction,
                    feature,
                    account,
                    "broker_preview_cost_or_risk_veto",
                    now=now,
                    payload={"v2": True, "candidate": chosen, **preview_detail},
                )
            else:
                try:
                    self.execution.execute(decision, chosen)
                    if setup_key:
                        self.journal.set_control("last_v2_agent_setup_key", setup_key)
                    self.journal.set_control("v2_pending_trade_thesis", "")
                except Exception as exc:
                    self.journal.alert(
                        "critical",
                        "V2 trader-agent execution failed",
                        str(exc),
                        "execution",
                    )
                    with contextlib.suppress(Exception):
                        self.publisher.alert(
                            "critical",
                            "V2 trader-agent execution failed",
                            str(exc),
                            "execution",
                        )

        self.journal.insert_decision(decision)
        self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, state_prediction, account)
        return (
            f"prediction={state_prediction['prediction_id']} action={decision['action']} "
            f"agent={plan_action} playbook={plan_playbook} "
            f"regime={lifecycle.get('current_regime')} source={lifecycle.get('source')} "
            f"chosen={'none' if chosen is None else chosen['strategy']} full={len(full_candidates)}"
        )

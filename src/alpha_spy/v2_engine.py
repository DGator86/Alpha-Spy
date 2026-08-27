from __future__ import annotations

import contextlib
import json
import statistics
from datetime import UTC, datetime
from typing import Any

import httpx

from .execution import build_multileg_payload
from .features import compute_features
from .prediction import create_prediction
from .risk import choose_decision, no_trade_decision
from .tradier import TradierClient, preview_fees
from .v2_learning import playbook_history
from .v2_services import V2EngineService as _BaseV2EngineService
from .v2_state_pq import generate_state_pq_candidates
from .v2_trader_agent import build_agent_plan

DEFAULT_BETA_V2_STATE_URL = "http://127.0.0.1:8790/api/state"
TRADER_AGENT_AUTHORITY = "alpha_v2_closed_loop_trader_agent"


class V2EngineService(_BaseV2EngineService):
    """Authoritative closed-loop Alpha trading-agent engine.

    Decision order is deliberately fixed:
    regime -> duration -> transition -> monetizable edge -> playbook -> entry timing
    -> exact option expression -> implementation economics -> execution.
    Positive option EV by itself is never permission to trade.
    """

    def __init__(self, config, journal, *, beta_state_url: str | None = None, **kwargs: Any):
        super().__init__(
            config,
            journal,
            beta_state_url=beta_state_url or DEFAULT_BETA_V2_STATE_URL,
            **kwargs,
        )
        # Multiple independent setups may be traded sequentially. This is a hard
        # operational guardrail, not a strategy rule; only one managed position may
        # still be open at a time through the normal risk gates.
        self.config.risk.maximum_trades_per_day = max(8, int(self.config.risk.maximum_trades_per_day))

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
            stamp = datetime.fromisoformat(
                str(opportunity.get("timestamp") or "").replace("Z", "+00:00")
            )
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        except ValueError:
            return None, "beta_v2_bad_timestamp"
        age = (now.astimezone(UTC) - stamp.astimezone(UTC)).total_seconds()
        if age < -5 or age > 120:
            return None, f"beta_v2_stale:{age:.0f}s"
        if opportunity.get("strategy_authority") not in (False, None):
            return None, "beta_v2_strategy_authority_violation"

        state_payload = opportunity.get("predictive_state") or {}
        regime = opportunity.get("regime_forecast") or {}
        state_ready = isinstance(state_payload, dict) and bool(state_payload.get("ready"))
        regime_ready = isinstance(regime, dict) and bool(regime.get("definable"))
        hgb = opportunity.get("hgb_direction") or {}
        hgb_ready = isinstance(hgb, dict) and bool(hgb.get("eligible"))
        if not state_ready and not hgb_ready:
            return None, "beta_v2_models_warming"
        if not regime_ready and not hgb_ready:
            # Alpha still records the undefined regime as a NO_TRADE observation.
            return opportunity, None
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
        base_prediction = create_prediction(self.journal, self.config, snapshot, feature)
        now = datetime.now(UTC)
        beta, beta_failure = self._beta_opportunity(now)
        account = self._account_state()

        if beta_failure:
            self.journal.insert_prediction(base_prediction)
            decision = no_trade_decision(
                base_prediction,
                feature,
                account,
                beta_failure,
                now=now,
                payload={"v2": True, "beta_state_url": self.beta_state_url},
            )
            self.journal.insert_decision(decision)
            self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
            self._publish(snapshot, feature, base_prediction, account)
            return f"prediction={base_prediction['prediction_id']} action=NO_TRADE reason={beta_failure}"

        assert beta is not None
        beta_prediction = self._attach_beta(base_prediction, beta)
        chain, options = self.journal.latest_option_chain("SPY")
        if chain and str(chain["captured_at"]) < str(snapshot["captured_at"]):
            options = []

        state_prediction, full_candidates = generate_state_pq_candidates(
            self.config,
            beta_prediction,
            beta,
            options,
            optimizer_config=self.optimizer_config,
        )
        self.journal.insert_prediction(state_prediction)

        history = playbook_history(self.journal)
        plan = build_agent_plan(
            beta,
            full_candidates,
            now=now,
            playbook_history=history,
        )
        chosen = plan.candidate if plan.action == "ENTER" else None

        if plan.thesis is not None:
            thesis = plan.thesis.as_dict()
            market_at_entry = {
                "spot": float(snapshot.get("spy_price") or 0.0),
                "atm_iv": self._atm_iv(options, float(snapshot.get("spy_price") or 0.0)),
                "captured_at": str(snapshot.get("captured_at") or ""),
                "beta_timestamp": beta.get("timestamp"),
            }
            thesis["market_at_entry"] = market_at_entry
            if plan.candidate is not None:
                candidate_payload = dict(plan.candidate.get("payload") or {})
                candidate_payload["authority"] = TRADER_AGENT_AUTHORITY
                candidate_payload["trade_thesis"] = thesis
                candidate_payload["trader_agent"] = {
                    "plan_action": plan.action,
                    "plan_reason": plan.reason,
                    "diagnostics": plan.diagnostics,
                    "playbook_history": history.get(plan.playbook, {}),
                }
                plan.candidate["payload"] = candidate_payload

        setup_key = plan.thesis.setup_key if plan.thesis is not None else ""
        already_traded = bool(setup_key and self.journal.get_control("last_v2_agent_setup_key") == setup_key)
        if already_traded and chosen is not None:
            chosen = None
            plan_reason = "setup_episode_already_traded"
        else:
            plan_reason = plan.reason

        authorized_id = chosen.get("candidate_id") if chosen is not None else None
        self._mark_candidates(full_candidates, authorized_id=authorized_id)
        self.journal.insert_candidates(full_candidates)

        if plan.action == "WAIT" and plan.thesis is not None:
            self.journal.set_control("v2_pending_trade_thesis", json.dumps(plan.thesis.as_dict(), separators=(",", ":")))
        elif plan.action == "NO_TRADE":
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
                        "action": plan.action,
                        "playbook": plan.playbook,
                        "diagnostics": plan.diagnostics,
                        "trade_thesis": plan.thesis.as_dict() if plan.thesis else None,
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
                "playbook": plan.playbook,
                "trade_thesis": plan.thesis.as_dict() if plan.thesis else None,
                "diagnostics": plan.diagnostics,
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
            f"agent={plan.action} playbook={plan.playbook} "
            f"chosen={'none' if chosen is None else chosen['strategy']} full={len(full_candidates)}"
        )

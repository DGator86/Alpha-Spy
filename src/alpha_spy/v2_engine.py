from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

import httpx

from .execution import build_multileg_payload
from .features import compute_features
from .prediction import create_prediction
from .risk import choose_decision, no_trade_decision
from .tradier import TradierClient, preview_fees
from .v2_hgb_vertical import build_hgb_vertical_candidate
from .v2_services import V2EngineService as _BaseV2EngineService
from .v2_state_pq import (
    authorize_state_pq_challenger,
    find_primary_valuation,
    generate_state_pq_candidates,
)

DEFAULT_BETA_V2_STATE_URL = "http://127.0.0.1:8790/api/state"
HGB_AUTHORITY = "beta_v2_hgb_blocked_walk_forward"
STATE_PQ_CHALLENGER_AUTHORITY = "alpha_v2_state_pq_challenger"
STATE_PQ_STATE_ONLY_AUTHORITY = "alpha_v2_state_pq_state_only"


class V2EngineService(_BaseV2EngineService):
    """Authoritative Alpha V2 state/P-Q champion-challenger engine.

    Beta publishes causal market evidence only. Alpha constructs empirical P from
    Beta's weighted historical analog outcomes while preserving the observed Q
    surface, values the complete 47-family bounded-risk universe, and lets a
    challenger replace the validated HGB two-point vertical only after it survives
    the execution-stress hurdle that killed the unrestricted allocator.
    """

    def __init__(self, config, journal, *, beta_state_url: str | None = None, **kwargs: Any):
        super().__init__(
            config,
            journal,
            beta_state_url=beta_state_url or DEFAULT_BETA_V2_STATE_URL,
            **kwargs,
        )

    def _beta_opportunity(self, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
        """Accept either a validated HGB trigger or a mature predictive state.

        HGB remains the incumbent directional authority. A ready predictive-state
        distribution is still allowed through so non-directional P/Q challengers
        can be counterfactually valued and, under deliberately strict thresholds,
        can eventually earn execution authority.
        """
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

        hgb = opportunity.get("hgb_direction") or {}
        hgb_ready = isinstance(hgb, dict) and bool(hgb.get("eligible"))
        predictive_state = opportunity.get("predictive_state") or {}
        state_ready = (
            isinstance(predictive_state, dict)
            and bool(predictive_state.get("ready"))
            and int(predictive_state.get("analog_count") or 0) >= 25
        )
        if not hgb_ready and not state_ready:
            return None, "beta_v2_models_warming_or_no_state"
        if hgb_ready and float(opportunity.get("trust") or 0.0) < 0.25:
            return None, "beta_v2_trust_below_threshold"
        return opportunity, None

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

        candidate_payload = candidate.get("payload") or {}
        authority = str(candidate_payload.get("authority") or "")
        if authority == HGB_AUTHORITY:
            estimated_fees = float(
                (candidate_payload.get("execution") or {}).get(
                    "estimated_roundtrip_fees_dollars"
                )
                or 0.0
            )
            risk_ex_fees = max(0.0, float(candidate.get("max_loss") or 0.0) - estimated_fees)
            preview_risk = risk_ex_fees + roundtrip
            ok = roundtrip <= 3.0 and preview_risk <= 100.0 + 1e-9
            return ok, {
                "preview": preview,
                "preview_roundtrip_fee_estimate": roundtrip,
                "preview_max_loss_dollars": preview_risk,
                "cost_source": "tradier_order_preview",
                "preview_policy": "hgb_execution_cost_and_risk_veto_only",
            }

        if authority in {STATE_PQ_CHALLENGER_AUTHORITY, STATE_PQ_STATE_ONLY_AUTHORITY}:
            selection = candidate_payload.get("state_pq_selection") or {}
            robust_ev = float(selection.get("challenger_robust_ev") or 0.0)
            preview_risk = float(candidate.get("max_loss") or 0.0) + roundtrip
            net_robust_after_preview = robust_ev - roundtrip
            ok = (
                roundtrip <= 5.0
                and preview_risk <= 100.0 + 1e-9
                and net_robust_after_preview > 0.0
            )
            return ok, {
                "preview": preview,
                "preview_roundtrip_fee_estimate": roundtrip,
                "preview_max_loss_dollars": preview_risk,
                "robust_ev_after_preview_fees": net_robust_after_preview,
                "cost_source": "tradier_order_preview",
                "preview_policy": "state_pq_stress_edge_cost_and_risk_veto",
            }

        net_after_preview = float(candidate.get("expected_value") or 0.0) - roundtrip
        return net_after_preview > 0.0, {
            "preview_roundtrip_fee_estimate": roundtrip,
            "expected_value_after_preview_fees": net_after_preview,
            "cost_source": "tradier_order_preview",
        }

    @staticmethod
    def _mark_shadow(
        candidates: list[dict[str, Any]],
        *,
        authorized_id: str | None = None,
        reason: str = "v2_state_pq_not_authorized",
    ) -> list[dict[str, Any]]:
        for candidate in candidates:
            if authorized_id and candidate.get("candidate_id") == authorized_id:
                payload = dict(candidate.get("payload") or {})
                payload["shadow_only"] = False
                payload["execution_authority"] = True
                candidate["payload"] = payload
                candidate["status"] = "ELIGIBLE"
                candidate["rejection_reason"] = None
                continue
            candidate["status"] = "SHADOW"
            candidate["rejection_reason"] = reason
            payload = dict(candidate.get("payload") or {})
            payload["shadow_only"] = True
            payload["execution_authority"] = False
            candidate["payload"] = payload
        return candidates

    @staticmethod
    def _grant_state_authority(
        candidate: dict[str, Any],
        selection: dict[str, Any],
        *,
        state_only: bool,
    ) -> dict[str, Any]:
        payload = dict(candidate.get("payload") or {})
        payload["authority"] = (
            STATE_PQ_STATE_ONLY_AUTHORITY if state_only else STATE_PQ_CHALLENGER_AUTHORITY
        )
        payload["execution_authority"] = True
        payload["shadow_only"] = False
        payload["forecast_horizon_minutes"] = 15
        payload["force_horizon_exit"] = True
        payload["state_pq_selection"] = selection
        candidate["payload"] = payload
        candidate["status"] = "ELIGIBLE"
        candidate["rejection_reason"] = None
        return candidate

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

        primary = build_hgb_vertical_candidate(state_prediction, beta, options)
        primary_valuation = find_primary_valuation(primary, full_candidates)
        challenger, selection = authorize_state_pq_challenger(
            primary,
            primary_valuation,
            full_candidates,
            beta,
            options,
        )

        chosen = primary
        if challenger is not None:
            chosen = self._grant_state_authority(
                challenger,
                selection,
                state_only=primary is None,
            )
            if primary is not None:
                self._mark_shadow(
                    [primary],
                    reason="state_pq_challenger_authorized",
                )
        elif primary is not None:
            primary.setdefault("payload", {})["state_pq_selection"] = selection
            primary.setdefault("payload", {})["execution_authority"] = True

        authorized_id = chosen.get("candidate_id") if chosen is not None else None
        self._mark_shadow(full_candidates, authorized_id=authorized_id)

        journal_candidates: list[dict[str, Any]] = []
        if primary is not None:
            journal_candidates.append(primary)
        journal_candidates.extend(full_candidates)
        self.journal.insert_candidates(journal_candidates)

        authorized_candidates = [chosen] if chosen is not None else []
        decision = choose_decision(
            self.config,
            self.journal,
            state_prediction,
            feature,
            authorized_candidates,
            account,
            now=now,
        )

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
                except Exception as exc:
                    self.journal.alert(
                        "critical",
                        "V2 state/P-Q execution failed",
                        str(exc),
                        "execution",
                    )
                    with contextlib.suppress(Exception):
                        self.publisher.alert(
                            "critical",
                            "V2 state/P-Q execution failed",
                            str(exc),
                            "execution",
                        )

        self.journal.insert_decision(decision)
        self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, state_prediction, account)
        return (
            f"prediction={state_prediction['prediction_id']} action={decision['action']} "
            f"primary={'none' if primary is None else primary['strategy']} "
            f"chosen={'none' if chosen is None else chosen['strategy']} "
            f"full={len(full_candidates)} lane={selection.get('lane')}"
        )

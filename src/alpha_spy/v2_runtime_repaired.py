from __future__ import annotations

from typing import Any

from . import v2_engine as engine_module
from .timeutil import ET
from .v2_hgb_vertical import build_hgb_vertical_candidate
from .v2_lifecycle_survival import AlphaRiskSetLifecycleEngine
from .v2_regime_repaired import classify_regime_hierarchy
from .v2_state_pq import generate_state_pq_candidates as _generate_state_pq_candidates
from .v2_trader_agent import PLAYBOOK_DIRECTIONAL
from .v2_trader_agent_repaired import build_agent_plan

_HGB_AUTHORITY = "beta_v2_hgb_blocked_walk_forward"
_DIRECTIONAL_DATE_CONTROL = "v2_directional_setup_session_date"


def _generate_candidates_with_control(
    config,
    prediction: dict[str, Any],
    beta_opportunity: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    optimizer_config=None,
):
    state_prediction, candidates = _generate_state_pq_candidates(
        config,
        prediction,
        beta_opportunity,
        options,
        optimizer_config=optimizer_config,
    )
    control = build_hgb_vertical_candidate(
        state_prediction,
        beta_opportunity,
        options,
    )
    if control is not None:
        payload = dict(control.get("payload") or {})
        payload["role"] = "validated_directional_incumbent"
        payload["competes_with_full_47_family_universe"] = True
        control["payload"] = payload
        candidates.append(control)
    return state_prediction, candidates


# Keep the mature V2 service implementation, but repair the three authorities
# exposed by chronological replay. Module globals are resolved at runtime by the
# base service, so these substitutions affect the inherited service without
# duplicating its large orchestration method.
engine_module.classify_regime_hierarchy = classify_regime_hierarchy
engine_module.AlphaRegimeLifecycleEngine = AlphaRiskSetLifecycleEngine
engine_module.generate_state_pq_candidates = _generate_candidates_with_control
engine_module.build_agent_plan = build_agent_plan


class V2EngineService(engine_module.V2EngineService):
    """Replay-repaired closed-loop paper trader.

    - Alpha regime uses tie-safe empirical ranks.
    - Step 3 uses blocked discrete-time risk-set survival.
    - Step 4 successor direction is advisory until scored calibration earns it.
    - Step 5 grants the validated HGB directional lane one setup per session.
    - P/Q remains authoritative for challenger geometry, not for revoking the HGB
      control signal.
    """

    def _market_intelligence(self, **kwargs: Any):
        market_state, alpha_regime, lifecycle = super()._market_intelligence(**kwargs)
        snapshot = kwargs["snapshot"]
        stamp = engine_module._parse_time(snapshot["captured_at"])
        session_date = stamp.astimezone(ET).date().isoformat()
        market_state["directional_setup_used_today"] = (
            self.journal.get_control(_DIRECTIONAL_DATE_CONTROL) == session_date
        )
        market_state["lifecycle_authority"] = "alpha_discrete_time_risk_set_survival"
        return market_state, alpha_regime, lifecycle

    def _preview_fee_gate(self, candidate: dict[str, Any]):
        ok, detail = super()._preview_fee_gate(candidate)
        payload = candidate.get("payload") or {}
        if str(payload.get("authority") or "") != _HGB_AUTHORITY:
            return ok, detail

        # The validated HGB control lane is not authorized by synthetic/state P/Q
        # expected value. Broker preview still has full authority over actual fee
        # and maximum-loss tolerability.
        if "preview_roundtrip_fee_estimate" not in detail:
            return ok, {
                **detail,
                "legacy_pq_ev_authority": False,
                "preview_policy": "hgb_control_fee_and_risk_only",
            }
        roundtrip = float(detail.get("preview_roundtrip_fee_estimate") or 0.0)
        preview_risk = float(detail.get("preview_max_loss_dollars") or 0.0)
        risk_limit = min(
            100.0,
            float(self.config.risk.maximum_trade_risk_dollars),
        )
        fee_risk_ok = roundtrip <= 5.0 and preview_risk <= risk_limit + 1e-9
        return fee_risk_ok, {
            **detail,
            "legacy_pq_ev_authority": False,
            "preview_policy": "hgb_control_fee_and_risk_only",
            "robust_ev_after_preview_fees": None,
        }

    def run_once(self) -> str | None:
        result = super().run_once()
        snapshot = self.journal.latest_snapshot()
        if snapshot:
            stamp = engine_module._parse_time(snapshot["captured_at"])
            session_date = stamp.astimezone(ET).date().isoformat()
            directional_key = "|".join(
                (session_date, PLAYBOOK_DIRECTIONAL, "FIRST_SETUP")
            )
            if self.journal.get_control("last_v2_agent_setup_key") == directional_key:
                self.journal.set_control(_DIRECTIONAL_DATE_CONTROL, session_date)
        return result

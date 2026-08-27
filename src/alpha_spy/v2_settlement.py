from __future__ import annotations

from datetime import datetime
from typing import Any

from . import hardening as hardening_module
from .hardening import HardenedSettlementService
from .position_management import (
    PositionManagementDecision,
    PositionSignal,
    evaluate_position as legacy_evaluate_position,
)

HGB_AUTHORITY = "beta_v2_hgb_blocked_walk_forward"


def _candidate_context(position: dict[str, Any]) -> dict[str, Any]:
    payload = position.get("payload") or {}
    candidate = payload.get("candidate") or {}
    inner = candidate.get("payload") or {}
    return inner if isinstance(inner, dict) else {}


def evaluate_v2_position(
    position: dict[str, Any],
    *,
    now: datetime,
    pnl: float,
    mfe: float,
    signal: PositionSignal,
) -> PositionManagementDecision:
    """Match the validated HGB trade to its exact fixed T+15 research horizon.

    Legacy positions retain the existing professional dynamic manager. HGB V2
    verticals deliberately do not take an early model-derived stop/target/trail:
    the profitable evidence was generated with a fixed 15-minute liquidation.
    Operator flatten, broker fail-safe, and the global 15:55 forced-flat remain
    outside this function in HardenedSettlementService.
    """
    context = _candidate_context(position)
    if str(context.get("authority") or "") != HGB_AUTHORITY:
        return legacy_evaluate_position(position, now=now, pnl=pnl, mfe=mfe, signal=signal)

    opened = datetime.fromisoformat(str(position["opened_at"]).replace("Z", "+00:00"))
    horizon = max(1, int(context.get("forecast_horizon_minutes") or 15))
    elapsed = max(0.0, (now - opened).total_seconds() / 60.0)
    should_exit = elapsed >= horizon
    reason = "forecast_horizon_exit" if should_exit else None
    return PositionManagementDecision(
        should_exit=should_exit,
        reason=reason,
        target_pnl=None,
        stop_pnl=None,
        trailing_floor=None,
        thesis_valid=True,
        state={
            "evaluated_at": now.isoformat(),
            "elapsed_minutes": elapsed,
            "progress": min(1.5, elapsed / horizon),
            "family": "directional_long",
            "authority": HGB_AUTHORITY,
            "fixed_horizon": True,
            "forecast_horizon_minutes": horizon,
            "target_pnl": None,
            "stop_pnl": None,
            "trailing_floor": None,
            "thesis_valid": True,
            "current_forecast_return": signal.forecast_return,
            "current_breadth": signal.breadth,
            "current_iv_edge_gap": signal.iv_edge_gap,
            "current_spot": signal.spot,
        },
    )


class V2SettlementService(HardenedSettlementService):
    """Settlement daemon that enforces the frozen HGB T+15 management contract."""

    def _manage_open_position(self) -> str:
        original = hardening_module.evaluate_position
        hardening_module.evaluate_position = evaluate_v2_position
        try:
            return super()._manage_open_position()
        finally:
            hardening_module.evaluate_position = original

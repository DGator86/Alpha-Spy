from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from math import isfinite
from typing import Any

from .timeutil import ET


@dataclass(frozen=True)
class PositionSignal:
    forecast_return: float
    breadth: float
    iv_edge_gap: float
    spot: float
    session_open: float = 0.0


@dataclass(frozen=True)
class PositionManagementDecision:
    should_exit: bool
    reason: str | None
    target_pnl: float | None
    stop_pnl: float | None
    trailing_floor: float | None
    thesis_valid: bool
    state: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _direction(strategy: str) -> int:
    upper = strategy.upper()
    bullish = any(token in upper for token in ("LONG_CALL", "CALL_DEBIT", "BULL_PUT"))
    bearish = any(token in upper for token in ("LONG_PUT", "PUT_DEBIT", "BEAR_CALL"))
    if bullish and not bearish:
        return 1
    if bearish and not bullish:
        return -1
    return 0


def _family(position: dict[str, Any]) -> str:
    candidate = position.get("payload", {}).get("candidate", {})
    family = candidate.get("payload", {}).get("family")
    if family:
        return str(family)
    strategy = str(position.get("strategy") or "")
    if strategy in {"LONG_CALL", "LONG_PUT", "CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"}:
        return "directional_long"
    if strategy in {"LONG_STRADDLE", "LONG_STRANGLE"}:
        return "long_vol"
    if strategy == "IRON_CONDOR":
        return "short_vol"
    if strategy in {"CALL_BUTTERFLY", "PUT_BUTTERFLY"}:
        return "range_debit"
    if strategy in {"BULL_PUT_CREDIT_SPREAD", "BEAR_CALL_CREDIT_SPREAD"}:
        return "directional_credit"
    return "conditional"


def _direction_invalid(direction: int, signal: PositionSignal) -> bool:
    if direction > 0:
        return signal.forecast_return < -0.00030 and signal.breadth < 0.46
    if direction < 0:
        return signal.forecast_return > 0.00030 and signal.breadth > 0.54
    return False


def _wing_geometry(position: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    legs = position.get("legs", [])
    short_puts = [float(x["strike"]) for x in legs if x.get("right") == "P" and str(x.get("side")).startswith("sell")]
    long_puts = [float(x["strike"]) for x in legs if x.get("right") == "P" and str(x.get("side")).startswith("buy")]
    short_calls = [float(x["strike"]) for x in legs if x.get("right") == "C" and str(x.get("side")).startswith("sell")]
    long_calls = [float(x["strike"]) for x in legs if x.get("right") == "C" and str(x.get("side")).startswith("buy")]
    return (
        max(short_puts) if short_puts else None,
        min(long_puts) if long_puts else None,
        min(short_calls) if short_calls else None,
        max(long_calls) if long_calls else None,
    )


def _opened_at(position: dict[str, Any]) -> datetime:
    value = datetime.fromisoformat(str(position["opened_at"]).replace("Z", "+00:00"))
    return value


def _entry_context(position: dict[str, Any]) -> dict[str, Any]:
    payload = position.get("payload")
    if not isinstance(payload, dict):
        return {}
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        return {}
    inner = candidate.get("payload")
    return inner if isinstance(inner, dict) else {}


def evaluate_position(
    position: dict[str, Any],
    *,
    now: datetime,
    pnl: float,
    mfe: float,
    signal: PositionSignal,
) -> PositionManagementDecision:
    """Live form of the professional exit policy used by the research layer.

    All thresholds are dollarized from the actual managed position. Persistent
    invalidation counters are returned in `state` and must be written back into the
    position payload so a one-minute polling loop cannot forget prior observations.
    """
    strategy = str(position["strategy"]).upper()
    family = _family(position)
    entry_kind = str(position.get("payload", {}).get("entry_kind") or "debit")
    quantity = max(1, int(position.get("quantity") or 1))
    entry_premium = float(position["entry_value"]) * 100.0 * quantity
    debit = entry_premium if entry_kind == "debit" else 0.0
    credit = entry_premium if entry_kind == "credit" else 0.0
    max_loss = max(float(position.get("max_loss") or 0.0) * quantity, 1e-9)
    max_profit = float(position.get("max_profit") or 0.0) * quantity
    candidate_payload = _entry_context(position)
    profit_unbounded = bool(candidate_payload.get("profit_unbounded", False))
    finite_max_profit = max_profit if not profit_unbounded and isfinite(max_profit) else float("inf")
    horizon = max(1, int(candidate_payload.get("forecast_horizon_minutes") or 15))
    elapsed = max(0.0, (now - _opened_at(position)).total_seconds() / 60.0)
    progress = min(1.5, elapsed / horizon)
    direction = _direction(strategy)

    prior = position.get("payload", {}).get("management_state", {})
    direction_invalid_count = int(prior.get("direction_invalid_count") or 0)
    edge_invalid_count = int(prior.get("edge_invalid_count") or 0)
    boundary_threat_count = int(prior.get("boundary_threat_count") or 0)

    target_pnl: float | None = None
    stop_pnl: float | None = None
    trailing_floor: float | None = None
    reason: str | None = None
    thesis_valid = True

    short_put, long_put, short_call, long_call = _wing_geometry(position)

    entry_spot = float(candidate_payload.get("entry_spot") or signal.spot)

    if entry_kind == "credit":
        if family == "short_vol" or "IRON_CONDOR" in strategy or "IRON_BUTTERFLY" in strategy:
            target_fraction = 0.45 if progress < 0.50 else 0.58
            target_pnl = max(2.50, target_fraction * credit)
            stop_amount = min(0.24 * max_loss, max(1.15 * credit, 0.10 * max_loss))
            stop_pnl = -stop_amount
            if abs(signal.spot - entry_spot) >= 1.40:
                reason = "range_impulse_abort"
            elif pnl >= target_pnl:
                reason = "range_profit_target"
            elif pnl <= stop_pnl:
                reason = "range_tail_stop"
            put_width = (short_put - long_put) if short_put is not None and long_put is not None else 1.0
            call_width = (long_call - short_call) if short_call is not None and long_call is not None else 1.0
            lower_threat = short_put is not None and signal.spot <= short_put + 0.30 * max(put_width, 1.0)
            upper_threat = short_call is not None and signal.spot >= short_call - 0.30 * max(call_width, 1.0)
            boundary_threat_count = boundary_threat_count + 1 if (lower_threat or upper_threat) else 0
            if reason is None and pnl < 0.0 and boundary_threat_count >= 2:
                reason = "range_boundary_exit"
            edge_invalid_count = edge_invalid_count + 1 if signal.iv_edge_gap > -0.0010 else 0
            if reason is None and elapsed >= 12 and edge_invalid_count >= 5 and pnl >= 0.0:
                reason = "short_vol_edge_invalidation"
            if mfe >= 0.38 * credit and pnl > 0.0:
                trailing_floor = mfe - max(0.08 * credit, 0.28 * mfe)
                if reason is None and pnl <= trailing_floor:
                    reason = "credit_profit_trail"
        else:
            target_fraction = 0.38 if progress < 0.55 else 0.52
            target_pnl = max(1.25, target_fraction * credit)
            stop_amount = min(0.22 * max_loss, max(1.10 * credit, 0.09 * max_loss))
            stop_pnl = -stop_amount
            if pnl >= target_pnl:
                reason = "credit_profit_target"
            elif pnl <= stop_pnl:
                reason = "credit_tail_stop"
            threatened = False
            if short_put is not None and long_put is not None:
                threatened = signal.spot <= short_put + 0.25 * max(short_put - long_put, 1.0)
            elif short_call is not None and long_call is not None:
                threatened = signal.spot >= short_call - 0.25 * max(long_call - short_call, 1.0)
            if reason is None and threatened and pnl < 0.0:
                reason = "pre_strike_tail_exit"
            invalid = _direction_invalid(direction, signal)
            direction_invalid_count = direction_invalid_count + 1 if invalid else 0
            thesis_valid = not invalid
            if reason is None and elapsed >= 6 and direction_invalid_count >= 3 and pnl < 0.05 * credit:
                reason = "direction_thesis_invalidation"
            edge_invalid_count = edge_invalid_count + 1 if signal.iv_edge_gap > 0.0010 else 0
            if reason is None and elapsed >= 12 and edge_invalid_count >= 5 and pnl >= 0.0:
                reason = "credit_edge_invalidation"
            if mfe >= 0.35 * credit and pnl > 0.0:
                trailing_floor = mfe - max(0.07 * credit, 0.25 * mfe)
                if reason is None and pnl <= trailing_floor:
                    reason = "credit_profit_trail"
            if reason is None and progress >= 0.72 and pnl < 0.12 * credit and abs(signal.forecast_return) < 0.00035:
                reason = "credit_time_stop"

    elif family == "directional_long":
        stop_fraction = 0.50 if progress < 0.25 else (0.42 if progress < 0.60 else 0.33)
        stop_pnl = -stop_fraction * debit
        if pnl <= stop_pnl:
            reason = "debit_risk_stop"
        if reason is None and isfinite(finite_max_profit):
            target_pnl = 0.78 * finite_max_profit
            if pnl >= target_pnl:
                reason = "vertical_profit_target"
        if mfe >= 0.35 * debit:
            trail_fraction = 0.22 if mfe >= 1.25 * debit else 0.32
            trailing_floor = mfe - max(0.12 * debit, trail_fraction * mfe)
            if reason is None and pnl <= trailing_floor:
                reason = "directional_profit_trail"
        invalid = _direction_invalid(direction, signal)
        thesis_valid = not invalid
        if reason is None and elapsed >= 6 and invalid and pnl < 0.15 * debit:
            reason = "direction_thesis_invalidation"
        if reason is None and elapsed >= 10 and signal.iv_edge_gap < -0.0025 and pnl <= 0.05 * debit:
            reason = "long_premium_edge_invalidation"
        sigma = max(float(candidate_payload.get("forecast_sigma_return") or 0.0), 1e-6)
        expected_move = max(entry_spot * sigma, 0.25)
        favorable_move = direction * (signal.spot - entry_spot) if direction else abs(signal.spot - entry_spot)
        entry_forecast = float(candidate_payload.get("forecast_expected_return") or 0.0)
        if (
            reason is None
            and favorable_move / expected_move >= 0.95
            and abs(signal.forecast_return) < 0.30 * max(abs(entry_forecast), 0.00025)
            and pnl > 0.20 * debit
        ):
            reason = "directional_move_complete"
        if reason is None and progress >= 0.58 and mfe < 0.18 * debit and pnl < -0.05 * debit:
            reason = "directional_time_stop"

    elif family in {"long_vol", "convex_tail"}:
        stop_fraction = 0.48 if progress < 0.35 else (0.40 if progress < 0.70 else 0.32)
        stop_pnl = -stop_fraction * debit
        if pnl <= stop_pnl:
            reason = "long_vol_risk_stop"
        if reason is None and isfinite(finite_max_profit):
            target_pnl = 0.80 * finite_max_profit
            if pnl >= target_pnl:
                reason = "capped_convex_profit_target"
        if mfe >= 0.50 * debit:
            trail_fraction = 0.20 if mfe >= 1.50 * debit else 0.30
            trailing_floor = mfe - max(0.15 * debit, trail_fraction * mfe)
            if reason is None and pnl <= trailing_floor:
                reason = "convex_profit_trail"
        if reason is None and elapsed >= 10 and signal.iv_edge_gap < -0.0010 and abs(signal.forecast_return) < 0.00045 and pnl <= 0.05 * debit:
            reason = "volatility_thesis_invalidation"
            thesis_valid = False
        sigma = max(float(candidate_payload.get("forecast_sigma_return") or 0.0), 1e-6)
        expected_move = max(entry_spot * sigma, 0.25)
        if reason is None and abs(signal.spot - entry_spot) / expected_move >= 1.10 and signal.iv_edge_gap <= 0.0 and pnl > 0.25 * debit:
            reason = "volatility_move_complete"
        if reason is None and progress >= 0.58 and mfe < 0.20 * debit and pnl < 0.0:
            reason = "long_vol_time_stop"

    elif family == "range_debit":
        stop_pnl = -0.42 * debit
        if pnl <= stop_pnl:
            reason = "range_debit_stop"
        if reason is None and isfinite(finite_max_profit):
            target_pnl = 0.58 * finite_max_profit
            if pnl >= target_pnl:
                reason = "range_debit_profit_target"
        strikes = sorted(float(leg["strike"]) for leg in position.get("legs", []))
        if reason is None and strikes and elapsed >= 8 and (signal.spot <= strikes[0] or signal.spot >= strikes[-1]) and pnl < 0.0:
            reason = "payoff_tent_invalidation"
            thesis_valid = False
        if reason is None and progress >= 0.62 and pnl <= 0.10 * debit:
            reason = "range_debit_time_stop"
        if mfe >= 0.30 * debit:
            trailing_floor = mfe - max(0.10 * debit, 0.30 * mfe)
            if reason is None and pnl <= trailing_floor:
                reason = "range_debit_profit_trail"

    else:
        risk_basis = debit if debit > 0 else max_loss
        stop_pnl = -0.40 * risk_basis
        if pnl <= stop_pnl:
            reason = "conditional_risk_stop"
        if reason is None and isfinite(finite_max_profit):
            target_pnl = 0.65 * finite_max_profit
            if pnl >= target_pnl:
                reason = "conditional_profit_target"
        invalid = _direction_invalid(direction, signal)
        thesis_valid = not invalid
        if reason is None and direction and elapsed >= 7 and invalid and pnl < 0.10 * risk_basis:
            reason = "conditional_thesis_invalidation"
        if mfe >= 0.35 * risk_basis:
            trailing_floor = mfe - max(0.12 * risk_basis, 0.30 * mfe)
            if reason is None and pnl <= trailing_floor:
                reason = "conditional_profit_trail"

    local_time = now.astimezone(ET).time().replace(tzinfo=None)
    if reason is None and local_time >= time(15, 50):
        if family == "short_vol" or "IRON_CONDOR" in strategy or "IRON_BUTTERFLY" in strategy:
            reason = "close_probe_exit"
    if reason is None and local_time >= time(15, 55):
        reason = "forced_flat_time"
    elif reason is None and local_time >= time(15, 48):
        risk_basis = max(debit, credit, 1.0)
        if pnl < 0.30 * risk_basis or abs(signal.forecast_return) < 0.00025:
            reason = "late_session_risk_reduction"

    # Do not chop a working directional into a 15-minute scalp. Horizon is
    # only a dead-trade stop; trail / move-complete / close probe handle winners.
    if reason is None and elapsed >= horizon:
        working = family == "directional_long" and mfe >= 0.18 * max(debit, 1.0) and pnl >= 0.0
        if not working:
            reason = "forecast_horizon_exit"

    state = {
        "evaluated_at": now.isoformat(),
        "elapsed_minutes": elapsed,
        "progress": progress,
        "family": family,
        "direction_invalid_count": direction_invalid_count,
        "edge_invalid_count": edge_invalid_count,
        "boundary_threat_count": boundary_threat_count,
        "target_pnl": target_pnl,
        "stop_pnl": stop_pnl,
        "trailing_floor": trailing_floor,
        "thesis_valid": thesis_valid,
        "current_forecast_return": signal.forecast_return,
        "current_breadth": signal.breadth,
        "current_iv_edge_gap": signal.iv_edge_gap,
        "current_spot": signal.spot,
    }
    return PositionManagementDecision(
        should_exit=reason is not None,
        reason=reason,
        target_pnl=target_pnl,
        stop_pnl=stop_pnl,
        trailing_floor=trailing_floor,
        thesis_valid=thesis_valid,
        state=state,
    )

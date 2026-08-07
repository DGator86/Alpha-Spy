from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable

import numpy as np


@dataclass(frozen=True)
class SignalState:
    forecast_return: float
    breadth: float
    concentration: float


@dataclass(frozen=True)
class ExitResult:
    cashflow: float
    minute: int
    spot: float
    reason: str
    mfe: float
    mae: float
    holding_minutes: int
    final_forecast_return: float
    final_breadth: float
    final_iv_gap: float


def _direction(name: str) -> int:
    upper = name.upper()
    bullish = any(token in upper for token in ("LONG_CALL", "CALL_DEBIT", "BULL_PUT", "CALL_BACK", "CALL_BROKEN"))
    bearish = any(token in upper for token in ("LONG_PUT", "PUT_DEBIT", "BEAR_CALL", "PUT_BACK", "PUT_BROKEN"))
    if bullish and not bearish:
        return 1
    if bearish and not bullish:
        return -1
    return 0


def _wing_geometry(candidate: Any) -> tuple[float | None, float | None, float | None, float | None]:
    short_puts = [float(k) for r, k, q in candidate.legs if r == "P" and q < 0]
    long_puts = [float(k) for r, k, q in candidate.legs if r == "P" and q > 0]
    short_calls = [float(k) for r, k, q in candidate.legs if r == "C" and q < 0]
    long_calls = [float(k) for r, k, q in candidate.legs if r == "C" and q > 0]
    short_put = max(short_puts) if short_puts else None
    long_put = min(long_puts) if long_puts else None
    short_call = min(short_calls) if short_calls else None
    long_call = max(long_calls) if long_calls else None
    return short_put, long_put, short_call, long_call


def _direction_invalid(direction: int, state: SignalState) -> bool:
    if direction > 0:
        return state.forecast_return < -0.00030 and state.breadth < 0.46
    if direction < 0:
        return state.forecast_return > 0.00030 and state.breadth > 0.54
    return False


def _result(
    *,
    cashflow: float,
    minute: int,
    spot: float,
    reason: str,
    mfe: float,
    mae: float,
    entry_minute: int,
    state: SignalState,
    iv_gap: float,
) -> ExitResult:
    return ExitResult(
        cashflow=float(cashflow),
        minute=int(minute),
        spot=float(spot),
        reason=reason,
        mfe=float(mfe),
        mae=float(mae),
        holding_minutes=int(minute - entry_minute),
        final_forecast_return=float(state.forecast_return),
        final_breadth=float(state.breadth),
        final_iv_gap=float(iv_gap),
    )


def professional_liquidation(
    candidate: Any,
    *,
    entry_minute: int,
    default_exit_minute: int,
    entry_spot: float,
    entry_forecast_return: float,
    entry_breadth: float,
    entry_model_p_iv: float,
    minutes_per_year: int,
    spy_prices: np.ndarray,
    market_iv: np.ndarray,
    market_skew: np.ndarray,
    model_q_iv: np.ndarray,
    signal_at: Callable[[int], SignalState],
    liquidation_cashflow: Callable[..., float],
) -> ExitResult:
    """One-minute, thesis-aware management for every finite-risk intraday structure.

    This routine intentionally uses only information available at or before each exit
    minute. It never references terminal price, future returns, hidden regime labels,
    event labels, or injected-dislocation labels.
    """
    name = str(candidate.name).upper()
    family = str(candidate.family).lower()
    credit_trade = float(candidate.entry_cashflow) < 0.0
    debit = max(float(candidate.entry_cashflow), 0.0)
    credit = max(-float(candidate.entry_cashflow), 0.0)
    max_loss = max(float(candidate.max_loss), 1e-9)
    finite_max_profit = float(candidate.max_profit) if isfinite(float(candidate.max_profit)) else np.inf
    direction = _direction(name)
    original_minutes = max(default_exit_minute - entry_minute, 1)
    entry_expected_move = max(
        entry_spot * max(float(entry_model_p_iv), 0.01) * np.sqrt(max(original_minutes / minutes_per_year, 1e-12)),
        0.25,
    )

    final_minute = int(default_exit_minute)
    final_spot = float(spy_prices[final_minute])
    final_cashflow = liquidation_cashflow(
        candidate,
        spot=final_spot,
        tenor=(390 - final_minute) / minutes_per_year,
        market_iv=float(market_iv[final_minute]),
        market_skew=float(market_skew[final_minute]),
    )
    final_state = signal_at(final_minute)
    final_iv_gap = float(model_q_iv[final_minute] - market_iv[final_minute])
    # MFE/MAE start at entry. The scheduled-exit mark is only a fallback and must
    # never seed the path statistics because that would leak future information.
    mfe = 0.0
    mae = 0.0

    short_put, long_put, short_call, long_call = _wing_geometry(candidate)
    direction_invalid_count = 0
    edge_invalid_count = 0
    boundary_threat_count = 0

    for minute in range(entry_minute + 1, default_exit_minute + 1):
        spot = float(spy_prices[minute])
        cashflow = liquidation_cashflow(
            candidate,
            spot=spot,
            tenor=(390 - minute) / minutes_per_year,
            market_iv=float(market_iv[minute]),
            market_skew=float(market_skew[minute]),
        )
        pnl = float(cashflow - candidate.entry_cashflow)
        mfe = max(mfe, pnl)
        mae = min(mae, pnl)
        elapsed = minute - entry_minute
        progress = elapsed / original_minutes
        state = signal_at(minute)
        iv_gap = float(model_q_iv[minute] - market_iv[minute])
        favorable_move = direction * (spot / entry_spot - 1.0) if direction else abs(spot / entry_spot - 1.0)

        # All finite-risk premium sellers: capture theta, cut tail acceleration, and
        # leave when either the directional thesis or relative-volatility thesis dies.
        if credit_trade:
            if family in {"short_vol", "adaptive_short_range"} or "IRON_CONDOR" in name or "IRON_BUTTERFLY" in name:
                target_fraction = 0.45 if progress < 0.50 else 0.58
                profit_target = max(0.025, target_fraction * credit)
                stop_loss = min(0.24 * max_loss, max(1.15 * credit, 0.10 * max_loss))
                if pnl >= profit_target:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="range_profit_target", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                if pnl <= -stop_loss:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="range_tail_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                put_width = (short_put - long_put) if short_put is not None and long_put is not None else 1.0
                call_width = (long_call - short_call) if short_call is not None and long_call is not None else 1.0
                lower_threat = short_put is not None and spot <= short_put + 0.30 * max(put_width, 1.0)
                upper_threat = short_call is not None and spot >= short_call - 0.30 * max(call_width, 1.0)
                boundary_threat_count = boundary_threat_count + 1 if (lower_threat or upper_threat) else 0
                if pnl < 0.0 and boundary_threat_count >= 2:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="range_boundary_exit", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                edge_invalid_count = edge_invalid_count + 1 if iv_gap > -0.0010 else 0
                if elapsed >= 12 and edge_invalid_count >= 5 and pnl >= 0.0:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="short_vol_edge_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                if mfe >= 0.38 * credit and pnl > 0.0 and pnl <= mfe - max(0.08 * credit, 0.28 * mfe):
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="credit_profit_trail", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            else:
                target_fraction = 0.38 if progress < 0.55 else 0.52
                profit_target = max(0.0125, target_fraction * credit)
                stop_loss = min(0.22 * max_loss, max(1.10 * credit, 0.09 * max_loss))
                if pnl >= profit_target:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="credit_profit_target", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                if pnl <= -stop_loss:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="credit_tail_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                if short_put is not None and long_put is not None:
                    buffer = 0.25 * max(short_put - long_put, 1.0)
                    threatened = spot <= short_put + buffer
                elif short_call is not None and long_call is not None:
                    buffer = 0.25 * max(long_call - short_call, 1.0)
                    threatened = spot >= short_call - buffer
                else:
                    threatened = False
                if threatened and pnl < 0.0:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="pre_strike_tail_exit", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                direction_invalid_count = direction_invalid_count + 1 if _direction_invalid(direction, state) else 0
                if elapsed >= 6 and direction_invalid_count >= 3 and pnl < 0.05 * credit:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="direction_thesis_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                # For credit trades a positive iv_gap means the market is no longer rich.
                edge_invalid_count = edge_invalid_count + 1 if iv_gap > 0.0010 else 0
                if elapsed >= 12 and edge_invalid_count >= 5 and pnl >= 0.0:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="credit_edge_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                if mfe >= 0.35 * credit and pnl > 0.0 and pnl <= mfe - max(0.07 * credit, 0.25 * mfe):
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="credit_profit_trail", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
                if progress >= 0.72 and pnl < 0.12 * credit and abs(state.forecast_return) < 0.00035:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="credit_time_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)

        # Directional long premium and debit verticals.
        elif family == "directional_long":
            stop_fraction = 0.50 if progress < 0.25 else (0.42 if progress < 0.60 else 0.33)
            if pnl <= -stop_fraction * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="debit_risk_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if isfinite(finite_max_profit) and pnl >= 0.78 * finite_max_profit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="vertical_profit_target", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if mfe >= 0.35 * debit:
                trail_fraction = 0.22 if mfe >= 1.25 * debit else 0.32
                giveback = max(0.12 * debit, trail_fraction * mfe)
                if pnl <= mfe - giveback:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="directional_profit_trail", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if elapsed >= 6 and _direction_invalid(direction, state) and pnl < 0.15 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="direction_thesis_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if elapsed >= 10 and iv_gap < -0.0025 and pnl <= 0.05 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="long_premium_edge_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            move_fraction = favorable_move * entry_spot / entry_expected_move
            if move_fraction >= 0.95 and abs(state.forecast_return) < 0.30 * max(abs(entry_forecast_return), 0.00025) and pnl > 0.20 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="directional_move_complete", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if progress >= 0.58 and mfe < 0.18 * debit and pnl < -0.05 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="directional_time_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)

        # Long-volatility structures: preserve convex winners while stopping theta bleed.
        elif family in {"long_vol", "convex_tail"}:
            stop_fraction = 0.48 if progress < 0.35 else (0.40 if progress < 0.70 else 0.32)
            if pnl <= -stop_fraction * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="long_vol_risk_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if isfinite(finite_max_profit) and pnl >= 0.80 * finite_max_profit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="capped_convex_profit_target", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if mfe >= 0.50 * debit:
                trail_fraction = 0.20 if mfe >= 1.50 * debit else 0.30
                giveback = max(0.15 * debit, trail_fraction * mfe)
                if pnl <= mfe - giveback:
                    return _result(cashflow=cashflow, minute=minute, spot=spot, reason="convex_profit_trail", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if elapsed >= 10 and iv_gap < -0.0010 and abs(state.forecast_return) < 0.00045 and pnl <= 0.05 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="volatility_thesis_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            realized_move_fraction = abs(spot - entry_spot) / entry_expected_move
            if realized_move_fraction >= 1.10 and iv_gap <= 0.0 and pnl > 0.25 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="volatility_move_complete", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if progress >= 0.58 and mfe < 0.20 * debit and pnl < 0.0:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="long_vol_time_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)

        # Debit butterflies and condors require price to migrate into the payoff tent.
        elif family == "range_debit":
            if pnl <= -0.42 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="range_debit_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if isfinite(finite_max_profit) and pnl >= 0.58 * finite_max_profit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="range_debit_profit_target", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            strikes = sorted(float(k) for _, k, _ in candidate.legs)
            lower, upper = strikes[0], strikes[-1]
            if elapsed >= 8 and (spot <= lower or spot >= upper) and pnl < 0.0:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="payoff_tent_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if progress >= 0.62 and pnl <= 0.10 * debit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="range_debit_time_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if mfe >= 0.30 * debit and pnl <= mfe - max(0.10 * debit, 0.30 * mfe):
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="range_debit_profit_trail", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)

        # Broken-wing and other conditional finite-risk structures.
        else:
            risk_basis = debit if debit > 0 else max_loss
            if pnl <= -0.40 * risk_basis:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="conditional_risk_stop", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if isfinite(finite_max_profit) and pnl >= 0.65 * finite_max_profit:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="conditional_profit_target", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if direction and elapsed >= 7 and _direction_invalid(direction, state) and pnl < 0.10 * risk_basis:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="conditional_thesis_invalidation", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)
            if mfe >= 0.35 * risk_basis and pnl <= mfe - max(0.12 * risk_basis, 0.30 * mfe):
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="conditional_profit_trail", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)

        # Last-seven-minute risk reduction: do not expose weak or marginal positions to
        # the most discontinuous part of 0DTE gamma unless the trade is still thriving.
        if minute >= min(default_exit_minute, 378):
            risk_basis = max(debit, credit, 0.01)
            if pnl < 0.30 * risk_basis or abs(state.forecast_return) < 0.00025:
                return _result(cashflow=cashflow, minute=minute, spot=spot, reason="late_session_risk_reduction", mfe=mfe, mae=mae, entry_minute=entry_minute, state=state, iv_gap=iv_gap)

        final_cashflow = cashflow
        final_minute = minute
        final_spot = spot
        final_state = state
        final_iv_gap = iv_gap

    return _result(
        cashflow=final_cashflow,
        minute=final_minute,
        spot=final_spot,
        reason="scheduled_exit",
        mfe=mfe,
        mae=mae,
        entry_minute=entry_minute,
        state=final_state,
        iv_gap=final_iv_gap,
    )

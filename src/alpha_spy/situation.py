"""Causal situation machine: the live program and the perfect book share this.

RANGE, IMPULSE, TREND, WATCH, WARMUP, CLOSED, UNKNOWN are computed only from
the RTH path seen so far. No lookahead. Structure choice, debit quality, and
hold-to-swing exits all read this state so Monday's strangle and Friday's
naked put cannot beat a $2–$3 debit or a $2-wing condor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .timeutil import ET

IMPULSE_DOLLARS = 1.40
REVERSAL_DOLLARS = 0.75
RANGE_DAY_DOLLARS = 3.0
MIN_RANGE_MINUTES = 90
MIN_HOLD_MINUTES = 8.0
WARMUP_MINUTES = 10
ENTRY_STOP_MINUTES = 370  # 15:40 ET
MIN_DEBIT_DOLLARS = 0.22
MIN_DEBIT_FRACTION = 0.07
PREFERRED_DEBIT_WIDTH = (2.0, 3.5)
TRAIL_AFTER_MAX_PROFIT_FRACTION = 0.50

DEFINED_DEBITS = {"CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"}
DISABLED_STRUCTURES = {
    "LONG_STRADDLE",
    "LONG_STRANGLE",
    "CALL_BUTTERFLY",
    "PUT_BUTTERFLY",
}
NAKED_LONGS = {"LONG_CALL", "LONG_PUT"}


@dataclass(frozen=True)
class SwingState:
    pivot: float
    extreme: float
    direction: int
    confirmed: bool
    tradeable: bool = False
    had_tradeable: bool = False
    reversed_from_tradeable: bool = False
    last_tradeable_direction: int = 0
    pending_direction: int = 0
    leg_started_at: datetime | None = None
    hold_minutes: float = 0.0
    leg_steps: int = 0


def empty_swing(spot: float = 0.0) -> SwingState:
    return SwingState(pivot=spot, extreme=spot, direction=0, confirmed=False)


def minutes_from_open(timestamp: datetime) -> int:
    eastern = timestamp.astimezone(ET)
    return (eastern.hour * 60 + eastern.minute) - (9 * 60 + 30)


def _hold_minutes(started_at: datetime | None, now: datetime | None, steps: int) -> float:
    if now is not None and started_at is not None:
        return max(0.0, (now - started_at).total_seconds() / 60.0)
    return float(max(0, steps))


def _with_leg(
    *,
    pivot: float,
    extreme: float,
    direction: int,
    prev: SwingState,
    now: datetime | None,
    impulse: float,
    reversed_from_tradeable: bool,
    new_leg: bool,
) -> SwingState:
    move = abs(float(extreme) - float(pivot)) if direction else 0.0
    confirmed = move >= impulse
    if new_leg:
        started = now
        steps = 1
        hold = 0.0 if now is not None else 1.0
    else:
        started = prev.leg_started_at if prev.leg_started_at is not None else now
        steps = prev.leg_steps + 1
        hold = _hold_minutes(started, now, steps)
    tradeable = confirmed and hold >= MIN_HOLD_MINUTES
    had = bool(prev.had_tradeable or prev.tradeable or tradeable)
    if tradeable:
        last_dir = direction
        pending = 0
    elif reversed_from_tradeable:
        last_dir = prev.direction if prev.tradeable else prev.last_tradeable_direction
        pending = direction
    elif new_leg:
        last_dir = prev.last_tradeable_direction
        pending = 0
    else:
        last_dir = prev.last_tradeable_direction
        pending = prev.pending_direction
    if prev.tradeable and not tradeable and not reversed_from_tradeable and not new_leg:
        last_dir = prev.direction
    return SwingState(
        pivot=pivot,
        extreme=extreme,
        direction=direction,
        confirmed=confirmed,
        tradeable=tradeable,
        had_tradeable=had,
        reversed_from_tradeable=reversed_from_tradeable,
        last_tradeable_direction=last_dir,
        pending_direction=pending,
        leg_started_at=started,
        hold_minutes=hold,
        leg_steps=steps,
    )


def swing_step(
    state: SwingState,
    spot: float,
    *,
    now: datetime | None = None,
    impulse: float = IMPULSE_DOLLARS,
    reversal: float = REVERSAL_DOLLARS,
) -> SwingState:
    """Classic zigzag: $0.75 reverses the pivot; $1.40 plus 8 minutes is tradeable."""
    px = float(spot)
    if state.pivot <= 0:
        return empty_swing(px)
    if state.direction == 0:
        if px == state.pivot:
            return state
        direction = 1 if px > state.pivot else -1
        return _with_leg(
            pivot=state.pivot,
            extreme=px,
            direction=direction,
            prev=state,
            now=now,
            impulse=impulse,
            reversed_from_tradeable=False,
            new_leg=True,
        )
    extending = (state.direction > 0 and px >= state.extreme) or (
        state.direction < 0 and px <= state.extreme
    )
    reversing = (state.direction > 0 and state.extreme - px >= reversal) or (
        state.direction < 0 and px - state.extreme >= reversal
    )
    if extending:
        return _with_leg(
            pivot=state.pivot,
            extreme=px,
            direction=state.direction,
            prev=state,
            now=now,
            impulse=impulse,
            reversed_from_tradeable=False,
            new_leg=False,
        )
    if reversing:
        return _with_leg(
            pivot=state.extreme,
            extreme=px,
            direction=-state.direction,
            prev=state,
            now=now,
            impulse=impulse,
            reversed_from_tradeable=state.tradeable,
            new_leg=True,
        )
    return _with_leg(
        pivot=state.pivot,
        extreme=state.extreme,
        direction=state.direction,
        prev=state,
        now=now,
        impulse=impulse,
        reversed_from_tradeable=False,
        new_leg=False,
    )


def swing_from_path(prices: list[float]) -> SwingState:
    if not prices:
        return empty_swing()
    state = empty_swing(float(prices[0]))
    for px in prices[1:]:
        state = swing_step(state, px)
    return state


def swing_from_tape(points: list[tuple[datetime, float]]) -> SwingState:
    if not points:
        return empty_swing()
    state = empty_swing(float(points[0][1]))
    for ts, px in points:
        state = swing_step(state, px, now=ts)
    return state


def classify_situation(
    *,
    minutes_open: int,
    session_range: float | None,
    confirmed_impulse: bool,
    trend_day: bool = False,
    had_tradeable_impulse: bool = False,
) -> str:
    """Causal regime. UNKNOWN means the tape is not in this snapshot; do not sit."""
    if minutes_open < 0 or minutes_open >= ENTRY_STOP_MINUTES:
        return "CLOSED"
    if confirmed_impulse:
        return "IMPULSE"
    if had_tradeable_impulse:
        return "TREND"
    if session_range is None:
        return "UNKNOWN"
    range_val = float(session_range)
    if minutes_open < WARMUP_MINUTES:
        return "WARMUP"
    if trend_day or range_val > RANGE_DAY_DOLLARS:
        return "TREND"
    if minutes_open >= MIN_RANGE_MINUTES and range_val <= RANGE_DAY_DOLLARS:
        return "RANGE"
    return "WATCH"


def debit_premium_ok(entry_price: float | None, width: float | None) -> bool:
    if entry_price is None or width is None:
        return False
    debit = float(entry_price)
    span = float(width)
    low, high = PREFERRED_DEBIT_WIDTH
    if span < low or span > high:
        return False
    return debit >= max(MIN_DEBIT_DOLLARS, MIN_DEBIT_FRACTION * span)


def trail_ready(mfe: float, max_profit: float) -> bool:
    if max_profit <= 0:
        return False
    return float(mfe) >= TRAIL_AFTER_MAX_PROFIT_FRACTION * float(max_profit)


def structure_situation_veto(
    name: str,
    family: str,
    *,
    situation: str,
    direction: int = 0,
) -> str | None:
    """None means the structure is allowed. UNKNOWN never vetoes."""
    if name in DISABLED_STRUCTURES or family == "long_vol":
        return "long_vol_disabled_tape_policy"
    if name in NAKED_LONGS:
        return "naked_long_disabled_tape_policy"
    if situation in {"UNKNOWN"}:
        return None
    if situation in {"WATCH", "WARMUP", "CLOSED"}:
        return f"situation_{situation.lower()}_sit"
    if situation == "RANGE":
        if name != "IRON_CONDOR" and family != "short_vol":
            return "range_day_condor_only"
        return None
    if situation == "IMPULSE":
        if name == "IRON_CONDOR" or family == "short_vol":
            return "impulse_blocks_condor"
        if name == "CALL_DEBIT_SPREAD" and direction < 0:
            return "impulse_direction_mismatch"
        if name == "PUT_DEBIT_SPREAD" and direction > 0:
            return "impulse_direction_mismatch"
        if name in DEFINED_DEBITS and direction == 0:
            return None
        return None
    if situation == "TREND":
        if name == "IRON_CONDOR" or family == "short_vol":
            return "trend_day_blocks_condor"
        if name == "CALL_DEBIT_SPREAD" and direction < 0:
            return "trend_direction_mismatch"
        if name == "PUT_DEBIT_SPREAD" and direction > 0:
            return "trend_direction_mismatch"
        if name in DEFINED_DEBITS and direction == 0:
            return "trend_wait_for_impulse"
        return None
    return None

"""Session-tape overlay learned from 2026-08-17.

A -35 bp grind never reclaimed the cash open. Alpha bought a call on a 5/15-minute
flicker while still below the open; Beta printed put signals but flattened every
15 minutes and sold condors on the trend. The rules here are the smallest overlay
that would have blocked the call, blocked short-vol, and held the put.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .timeutil import ET, utc_iso

# Once SPY is this far through the open, 0DTE directionals must agree with it.
SESSION_BIAS_BPS = 6.0
# Hold the structure for the session instead of the 15-minute forecast horizon.
SESSION_HOLD_BPS = 8.0
# Do not sell defined-risk premium this far from the open / VWAP.
SESSION_SHORT_VOL_BPS = 12.0


def distance_bps(spot: float | None, reference: float | None) -> float | None:
    if spot is None or reference is None or spot <= 0 or reference <= 0:
        return None
    return (float(spot) / float(reference) - 1.0) * 10_000.0


def blocks_bullish(open_bps: float | None, *, threshold: float = SESSION_BIAS_BPS) -> bool:
    return open_bps is not None and open_bps <= -threshold


def blocks_bearish(open_bps: float | None, *, threshold: float = SESSION_BIAS_BPS) -> bool:
    return open_bps is not None and open_bps >= threshold


def blocks_short_vol(
    open_bps: float | None,
    vwap_bps: float | None = None,
    *,
    threshold: float = SESSION_SHORT_VOL_BPS,
) -> bool:
    magnitudes = [abs(value) for value in (open_bps, vwap_bps) if value is not None]
    return bool(magnitudes) and max(magnitudes) >= threshold


def session_agrees_with_direction(
    direction: int,
    open_bps: float | None,
    *,
    threshold: float = SESSION_HOLD_BPS,
) -> bool:
    """True when the cash session is still working for an open directional debit."""
    if open_bps is None or direction == 0:
        return False
    if direction < 0:
        return open_bps <= -threshold
    return open_bps >= threshold


def structure_session_veto(
    name: str,
    family: str,
    *,
    open_bps: float | None,
    vwap_bps: float | None = None,
    bias_bps: float = SESSION_BIAS_BPS,
    short_vol_bps: float = SESSION_SHORT_VOL_BPS,
) -> str | None:
    bullish = name in {"LONG_CALL", "CALL_DEBIT_SPREAD", "BULL_PUT_CREDIT_SPREAD"}
    bearish = name in {"LONG_PUT", "PUT_DEBIT_SPREAD", "BEAR_CALL_CREDIT_SPREAD"}
    if family in {"directional_long", "directional_credit"}:
        if bullish and blocks_bullish(open_bps, threshold=bias_bps):
            return "session_bias_against_calls"
        if bearish and blocks_bearish(open_bps, threshold=bias_bps):
            return "session_bias_against_puts"
    if family == "short_vol" and blocks_short_vol(open_bps, vwap_bps, threshold=short_vol_bps):
        return "session_trend_blocks_short_vol"
    return None


def resolve_session_open_spy(journal: Any, snapshot: dict[str, Any]) -> float | None:
    """First RTH SPY print of the session, cached on control_state.

    Falls back to the current snapshot only when no opening print exists yet
    (engine started after the bell). Callers must not treat a mid-session
    fallback as gospel for historical replay.
    """
    captured_raw = snapshot.get("captured_at")
    if not captured_raw:
        return None
    captured = datetime.fromisoformat(str(captured_raw).replace("Z", "+00:00"))
    local = captured.astimezone(ET)
    minutes_from_open = (local.hour * 60 + local.minute) - (9 * 60 + 30)
    if minutes_from_open < 0:
        return None
    key = f"rth_open_spy:{local.date().isoformat()}"
    cached = journal.get_control(key)
    if cached:
        try:
            value = float(cached)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    start = datetime(local.year, local.month, local.day, 9, 30, tzinfo=ET)
    end = start + timedelta(minutes=5)
    start_utc = utc_iso(start.astimezone(UTC))
    end_utc = utc_iso(end.astimezone(UTC))
    with journal.session() as con:
        row = con.execute(
            """
            SELECT spy_price FROM market_snapshots
            WHERE captured_at >= ? AND captured_at < ? AND spy_price > 0
            ORDER BY captured_at ASC LIMIT 1
            """,
            (start_utc, end_utc),
        ).fetchone()
    if row and float(row[0] or 0.0) > 0:
        price = float(row[0])
        journal.set_control(key, f"{price:.6f}")
        return price
    # Only the first five minutes may seed the open from the live print.
    # A mid-session restart must not rewrite 9:30 as the 2pm price.
    if minutes_from_open <= 5:
        price = float(snapshot.get("spy_price") or 0.0)
        state = str(snapshot.get("exchange_state") or "").lower()
        if price > 0 and state == "open":
            journal.set_control(key, f"{price:.6f}")
            return price
    return None

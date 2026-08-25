"""Fail-closed guards so Friday Aug 21 cannot repeat.

These helpers are pure. Settlement, ranking, execution, and the engine import
them so a type error, a sandbox 400, or a yaml tweak cannot disable flatten,
wipe calibration, or let a naked long beat a $2–$3 debit.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from typing import Any

from .position_management import PositionSignal
from .timeutil import ET, at_or_after_et

IMPULSE_DOLLARS = 1.40
PREFERRED_DEBIT_WIDTH = (2.0, 3.5)
MANAGEMENT_ERROR_FLATTEN = 3
CONDOR_MIN_WING = 2.0
CONDOR_SHORT_CLEARANCE = 2.0
WATCHDOG_FLAT_TIME_ET = "16:00"
DEFINED_DEBITS = {"CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"}
LONG_TO_DEBIT = {"LONG_CALL": "CALL_DEBIT_SPREAD", "LONG_PUT": "PUT_DEBIT_SPREAD"}


def build_position_signal(**kwargs: Any) -> PositionSignal:
    """Construct a PositionSignal while ignoring unknown/legacy kwargs.

    Friday died because hardening still passed session_open after the dataclass
    dropped the field. Extra keys are discarded; missing keys default safely.
    """
    accepted = {item.name for item in fields(PositionSignal)}
    filtered = {key: value for key, value in kwargs.items() if key in accepted}
    return PositionSignal(
        forecast_return=float(filtered.get("forecast_return") or 0.0),
        breadth=float(filtered.get("breadth") or 0.5),
        iv_edge_gap=float(filtered.get("iv_edge_gap") or 0.0),
        spot=float(filtered.get("spot") or 0.0),
        session_open=float(filtered.get("session_open") or 0.0),
        vwap_distance_bps=float(filtered.get("vwap_distance_bps") or 0.0),
        situation_tradeable=bool(filtered.get("situation_tradeable") or False),
    )


def must_fail_safe_flatten(
    *,
    now: datetime,
    forced_flat_time_et: str,
    error_count: int,
    flatten_requested: bool = False,
    opened_at: datetime | None = None,
) -> bool:
    if flatten_requested:
        return True
    if opened_at is not None and opened_at.astimezone(ET).date() < now.astimezone(ET).date():
        return True
    if int(error_count) >= MANAGEMENT_ERROR_FLATTEN:
        return True
    if at_or_after_et(now, forced_flat_time_et):
        return True
    return at_or_after_et(now, WATCHDOG_FLAT_TIME_ET)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def session_range_impulse(session_high: float | None, session_low: float | None) -> bool:
    if session_high is None or session_low is None:
        return False
    return (float(session_high) - float(session_low)) >= IMPULSE_DOLLARS


def impulse_allows_off_grid(
    *,
    session_high: float | None,
    session_low: float | None,
    spot: float,
    last_impulse_spot: float | None,
) -> bool:
    if not session_range_impulse(session_high, session_low):
        return False
    if last_impulse_spot is None:
        return True
    return abs(float(spot) - float(last_impulse_spot)) >= IMPULSE_DOLLARS


def debit_width_ok(width: float | None) -> bool:
    if width is None:
        return False
    low, high = PREFERRED_DEBIT_WIDTH
    return low <= float(width) <= high


def prefer_defined_debits(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    constructed = {
        str(row.get("strategy"))
        for row in candidates
        if str(row.get("strategy")) in DEFINED_DEBITS and debit_width_ok(row.get("width"))
    }
    for row in candidates:
        name = str(row.get("strategy") or "")
        required = LONG_TO_DEBIT.get(name)
        if not required or row.get("status") != "ELIGIBLE":
            continue
        prior = str(row.get("rejection_reason") or "")
        reason = "defined_debit_preferred" if required in constructed else "naked_long_disabled"
        row["status"] = "REJECTED"
        row["rejection_reason"] = ",".join(part for part in (prior, reason) if part)
    return candidates


def condor_geometry_ok(
    *,
    put_width: float,
    call_width: float,
    short_put: float | None,
    short_call: float | None,
    spot: float | None,
) -> bool:
    if put_width < CONDOR_MIN_WING or call_width < CONDOR_MIN_WING:
        return False
    if spot is None:
        return True
    if short_put is not None and abs(float(short_put) - float(spot)) < CONDOR_SHORT_CLEARANCE:
        return False
    if short_call is not None and abs(float(short_call) - float(spot)) < CONDOR_SHORT_CLEARANCE:
        return False
    return True


def reject_unsafe_condors(candidates: list[dict[str, Any]], spot: float) -> list[dict[str, Any]]:
    for row in candidates:
        if str(row.get("strategy")) != "IRON_CONDOR":
            continue
        legs = row.get("legs") or []
        short_puts = [
            float(leg["strike"])
            for leg in legs
            if leg.get("right") == "P" and str(leg.get("side") or "").startswith("sell")
        ]
        long_puts = [
            float(leg["strike"])
            for leg in legs
            if leg.get("right") == "P" and str(leg.get("side") or "").startswith("buy")
        ]
        short_calls = [
            float(leg["strike"])
            for leg in legs
            if leg.get("right") == "C" and str(leg.get("side") or "").startswith("sell")
        ]
        long_calls = [
            float(leg["strike"])
            for leg in legs
            if leg.get("right") == "C" and str(leg.get("side") or "").startswith("buy")
        ]
        put_width = (max(short_puts) - min(long_puts)) if short_puts and long_puts else 0.0
        call_width = (max(long_calls) - min(short_calls)) if short_calls and long_calls else 0.0
        ok = condor_geometry_ok(
            put_width=put_width,
            call_width=call_width,
            short_put=max(short_puts) if short_puts else None,
            short_call=min(short_calls) if short_calls else None,
            spot=spot,
        )
        if not ok and row.get("status") == "ELIGIBLE":
            prior = str(row.get("rejection_reason") or "")
            row["status"] = "REJECTED"
            row["rejection_reason"] = ",".join(
                part for part in (prior, "condor_wings_or_clearance_unsafe") if part
            )
    return candidates


def spot_impulse_from_entry(entry_spot: float, spot: float) -> bool:
    return abs(float(spot) - float(entry_spot)) >= IMPULSE_DOLLARS


def sandbox_reject_allows_paper_fallback(
    *,
    paper_mode: bool,
    environment: str,
    status_code: int | None,
) -> bool:
    if not paper_mode:
        return False
    if str(environment).lower() != "sandbox":
        return False
    if status_code is None:
        return True
    return int(status_code) in {400, 409, 422}

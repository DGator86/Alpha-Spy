from __future__ import annotations

import math
from typing import Any

_LOWER_BOUND_TOLERANCE = 0.02
_UPPER_BOUND_TOLERANCE = 0.05


def option_price_sane(option: dict[str, Any], spot: float) -> bool:
    """Conservative executable-price sanity for same-day SPY options.

    This is deliberately not an option pricer. It only removes observations that
    violate elementary price bounds badly enough that downstream P/Q optimization
    could manufacture edge from a stale/corrupt quote.
    """
    try:
        strike = float(option.get("strike") or 0.0)
        bid = float(option.get("bid") or 0.0)
        ask = float(option.get("ask") or 0.0)
    except (TypeError, ValueError):
        return False
    right = str(option.get("right") or "")
    if right not in {"C", "P"} or spot <= 0.0 or strike <= 0.0:
        return False
    if not all(math.isfinite(value) for value in (spot, strike, bid, ask)):
        return False
    if bid < 0.0 or ask <= 0.0 or ask < bid:
        return False

    if right == "C":
        intrinsic = max(spot - strike, 0.0)
        upper = spot
    else:
        intrinsic = max(strike - spot, 0.0)
        upper = strike
    if ask + _LOWER_BOUND_TOLERANCE < intrinsic:
        return False
    if ask > upper + _UPPER_BOUND_TOLERANCE:
        return False

    delta = option.get("delta")
    if delta not in (None, ""):
        try:
            parsed_delta = float(delta)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(parsed_delta):
            return False
        if right == "C" and not 0.0 <= parsed_delta <= 1.0:
            return False
        if right == "P" and not -1.0 <= parsed_delta <= 0.0:
            return False
    return True


def sane_option_surface(options: list[dict[str, Any]], spot: float) -> list[dict[str, Any]]:
    return [option for option in options if option_price_sane(option, spot)]

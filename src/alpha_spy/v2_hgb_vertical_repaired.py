from __future__ import annotations

import math
from typing import Any

from .v2_hgb_vertical import (
    HGBVerticalConfig,
    build_hgb_vertical_candidate as _build_candidate,
)

_INTRINSIC_TOLERANCE = 0.02


def _delta(option: dict[str, Any]) -> float | None:
    value = option.get("delta")
    if value in (None, ""):
        payload = option.get("payload") or {}
        value = payload.get("delta") if isinstance(payload, dict) else None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def option_quote_sane(option: dict[str, Any], spot: float) -> bool:
    """Basic no-arbitrage sanity for the real 0DTE quote used by HGB geometry."""
    try:
        strike = float(option.get("strike") or 0.0)
        bid = float(option.get("bid") or 0.0)
        ask = float(option.get("ask") or 0.0)
    except (TypeError, ValueError):
        return False
    right = str(option.get("right") or "")
    if right not in {"C", "P"} or spot <= 0.0 or strike <= 0.0:
        return False
    if not all(math.isfinite(value) for value in (strike, bid, ask)):
        return False
    if bid < 0.0 or ask <= 0.0 or ask < bid:
        return False

    intrinsic = max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    if ask + _INTRINSIC_TOLERANCE < intrinsic:
        return False

    delta = _delta(option)
    if delta is None:
        return False
    if right == "C":
        return 0.0 <= delta <= 1.0
    return -1.0 <= delta <= 0.0


def build_hgb_vertical_candidate(
    prediction: dict[str, Any],
    beta_opportunity: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    config: HGBVerticalConfig | None = None,
) -> dict[str, Any] | None:
    spot = float(prediction.get("spy_price") or 0.0)
    sane = [row for row in options if option_quote_sane(row, spot)]
    return _build_candidate(
        prediction,
        beta_opportunity,
        sane,
        config=config,
    )

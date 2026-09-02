from __future__ import annotations

import math
from typing import Any

from . import strategy_v2 as base


_BASE_LIQUIDITY_SCORE = base._liquidity_score


def _displayed_size(option: dict[str, Any], key: str) -> int:
    direct = option.get(key)
    if direct not in (None, ""):
        try:
            return max(0, int(float(direct)))
        except (TypeError, ValueError):
            pass
    payload = option.get("payload") or {}
    raw_key = "bidsize" if key == "bid_size" else "asksize"
    try:
        return max(0, int(float(payload.get(raw_key) or 0)))
    except (TypeError, ValueError, AttributeError):
        return 0


def liquidity_score(option: dict[str, Any], spot: float, cfg: base.V2OptimizerConfig) -> float:
    score = _BASE_LIQUIDITY_SCORE(option, spot, cfg)
    bid_size = _displayed_size(option, "bid_size")
    ask_size = _displayed_size(option, "ask_size")
    two_sided = min(bid_size, ask_size)
    depth_bonus = 0.06 * math.log1p(two_sided)
    one_sided_penalty = 0.15 if bid_size <= 0 or ask_size <= 0 else 0.0
    return score + depth_bonus - one_sided_penalty


# Base `liquid_contract_pool` resolves this function dynamically.
base._liquidity_score = liquidity_score

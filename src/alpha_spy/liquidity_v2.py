from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OptionLiquidity:
    symbol: str
    spread: float
    relative_spread: float
    quoted_size: int
    open_interest: int
    volume: int
    premium: float
    score: float


def option_liquidity(option: dict[str, Any]) -> OptionLiquidity | None:
    bid = float(option.get("bid") or 0.0)
    ask = float(option.get("ask") or 0.0)
    if bid < 0 or ask <= 0 or ask < bid:
        return None
    mid = float(option.get("midpoint") or ((bid + ask) / 2.0) or 0.0)
    if mid <= 0:
        return None
    spread = max(0.0, ask - bid)
    relative = spread / max(mid, 0.01)
    bid_size = int(option.get("bid_size") or 0)
    ask_size = int(option.get("ask_size") or 0)
    quoted_size = max(0, min(bid_size, ask_size))
    oi = int(option.get("open_interest") or 0)
    volume = int(option.get("volume") or 0)

    # Execution drag dominates a 15-minute trade.  Penny-wide contracts get a
    # large advantage; depth, OI and volume are logarithmic so they cannot rescue
    # a structurally wide market.
    spread_penalty = 12.0 * spread + 3.0 * relative
    depth_bonus = 0.05 * math.log1p(quoted_size)
    oi_bonus = 0.04 * math.log1p(oi)
    volume_bonus = 0.04 * math.log1p(volume)
    score = depth_bonus + oi_bonus + volume_bonus - spread_penalty
    return OptionLiquidity(
        symbol=str(option.get("symbol") or ""),
        spread=spread,
        relative_spread=relative,
        quoted_size=quoted_size,
        open_interest=oi,
        volume=volume,
        premium=mid,
        score=score,
    )


def liquid_option_pool(
    options: list[dict[str, Any]],
    *,
    spot: float,
    max_distance_dollars: float = 12.0,
    max_spread_dollars: float = 0.05,
    max_relative_spread: float = 0.25,
    min_open_interest: int = 10,
    min_volume: int = 0,
    per_right_limit: int = 28,
) -> list[dict[str, Any]]:
    ranked: list[tuple[float, dict[str, Any]]] = []
    for option in options:
        strike = float(option.get("strike") or 0.0)
        if strike <= 0 or abs(strike - spot) > max_distance_dollars:
            continue
        liq = option_liquidity(option)
        if liq is None:
            continue
        if liq.spread > max_spread_dollars:
            continue
        if liq.relative_spread > max_relative_spread:
            continue
        if liq.open_interest < min_open_interest or liq.volume < min_volume:
            continue
        # Keep the most liquid contracts, with a small proximity bonus because
        # 0DTE liquidity tends to concentrate around spot.
        proximity = 1.0 / (1.0 + abs(strike - spot))
        ranked.append((liq.score + 0.15 * proximity, option))

    out: list[dict[str, Any]] = []
    for right in ("C", "P"):
        rows = [item for item in ranked if str(item[1].get("right")) == right]
        rows.sort(key=lambda item: item[0], reverse=True)
        out.extend(option for _, option in rows[:per_right_limit])
    return sorted(out, key=lambda option: (str(option.get("right")), float(option.get("strike") or 0.0)))


def structure_execution_drag(legs: list[tuple[dict[str, Any], str]]) -> float:
    """Quoted one-way spread drag in dollars per 1x structure.

    Entry valuation already crosses executable bid/ask.  This metric exists for
    pre-screening/ranking so a nominally attractive four-leg structure cannot
    dominate a one-leg expression unless the extra payoff is worth the extra market.
    """
    drag = 0.0
    for option, _side in legs:
        quantity = 1
        ask = float(option.get("ask") or 0.0)
        bid = float(option.get("bid") or 0.0)
        drag += max(0.0, ask - bid) * 100.0 * quantity
    return drag

from __future__ import annotations

from datetime import datetime

import pandas as pd


def option_quote_quality(
    chain: pd.DataFrame,
    *,
    now: datetime,
    max_age_ms: int,
    max_relative_spread: float,
    min_open_interest: int,
    min_volume: int,
) -> pd.Series:
    timestamp = pd.to_datetime(chain["timestamp"], utc=True)
    age_ms = (pd.Timestamp(now).tz_convert("UTC") - timestamp).dt.total_seconds() * 1000.0
    midpoint = (chain["bid"] + chain["ask"]) / 2.0
    spread = chain["ask"] - chain["bid"]
    relative_spread = spread / midpoint.clip(lower=0.01)
    return (
        chain["bid"].ge(0)
        & chain["ask"].ge(chain["bid"])
        & age_ms.le(max_age_ms)
        & relative_spread.le(max_relative_spread)
        & chain["open_interest"].ge(min_open_interest)
        & chain["volume"].ge(min_volume)
        & chain["implied_volatility"].gt(0)
    )

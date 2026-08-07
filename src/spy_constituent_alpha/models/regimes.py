from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

import pandas as pd


@dataclass(frozen=True)
class RegimeState:
    volatility: str
    correlation: str
    breadth: str
    concentration: str
    session: str
    event: str

    @property
    def key(self) -> str:
        return "|".join(
            [
                self.volatility,
                self.correlation,
                self.breadth,
                self.concentration,
                self.session,
                self.event,
            ]
        )


def classify_regime(
    *,
    timestamp: datetime,
    realized_vol: float,
    vol_history: pd.Series,
    average_correlation: float,
    correlation_history: pd.Series,
    weighted_advance: float,
    top_n_contribution: float,
    event: str = "ordinary",
) -> RegimeState:
    """Deterministic baseline regime classifier suitable for point-in-time backtests."""
    vol_hist = vol_history.dropna()
    corr_hist = correlation_history.dropna()
    vol_q = float((vol_hist <= realized_vol).mean()) if len(vol_hist) else 0.5
    corr_q = float((corr_hist <= average_correlation).mean()) if len(corr_hist) else 0.5

    volatility = "low" if vol_q < 0.25 else "normal" if vol_q < 0.75 else "high" if vol_q < 0.95 else "crisis"
    if len(corr_hist) >= 20:
        recent = corr_hist.tail(5).mean()
        prior = corr_hist.tail(20).head(15).mean()
        delta = recent - prior
    else:
        delta = 0.0
    if corr_q > 0.95:
        correlation = "dislocated"
    elif delta > 0.03:
        correlation = "rising"
    elif delta < -0.03:
        correlation = "falling"
    else:
        correlation = "stable"

    if weighted_advance >= 0.70:
        breadth = "broad_up"
    elif weighted_advance <= 0.30:
        breadth = "broad_down"
    else:
        breadth = "mixed"
    concentration = "concentrated" if top_n_contribution >= 0.65 else "distributed"

    local = timestamp.timetz().replace(tzinfo=None)
    if local < time(10, 0):
        session = "opening"
    elif local < time(15, 0):
        session = "midday"
    elif local < time(15, 50):
        session = "final_hour"
    else:
        session = "expiration_window"

    return RegimeState(
        volatility=volatility,
        correlation=correlation,
        breadth=breadth,
        concentration=concentration,
        session=session,
        event=event,
    )

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .interfaces import MarketDataProvider


class CsvMarketDataProvider(MarketDataProvider):
    """Simple adapter for research files and vendor exports."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def point_in_time_weights(self, timestamp: datetime) -> pd.Series:
        frame = pd.read_csv(self.root / "weights.csv", parse_dates=["effective_at"])
        frame = frame[frame["effective_at"] <= pd.Timestamp(timestamp)]
        if frame.empty:
            raise ValueError("No point-in-time weights are available before the requested timestamp")
        effective = frame["effective_at"].max()
        snap = frame[frame["effective_at"] == effective]
        weights = snap.set_index("ticker")["weight"].astype(float)
        return weights / weights.sum()

    def equity_bars(self, start: datetime, end: datetime, tickers: list[str]) -> pd.DataFrame:
        frame = pd.read_parquet(self.root / "equity_bars.parquet")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        mask = (
            frame["ticker"].isin(tickers)
            & (frame["timestamp"] >= pd.Timestamp(start))
            & (frame["timestamp"] <= pd.Timestamp(end))
        )
        return frame.loc[mask].set_index(["timestamp", "ticker"]).sort_index()

    def option_chain(self, underlying: str, timestamp: datetime) -> pd.DataFrame:
        frame = pd.read_parquet(self.root / "options.parquet")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame[frame["underlying"] == underlying]
        before = frame[frame["timestamp"] <= pd.Timestamp(timestamp)]
        if before.empty:
            raise ValueError(f"No option chain for {underlying} at or before {timestamp}")
        snapshot_time = before["timestamp"].max()
        return before[before["timestamp"] == snapshot_time].copy()

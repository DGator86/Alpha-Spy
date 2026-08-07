from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class MarketDataProvider(ABC):
    """Vendor-neutral market-data boundary."""

    @abstractmethod
    def point_in_time_weights(self, timestamp: datetime) -> pd.Series:
        """Return ticker-indexed weights valid at the supplied timestamp."""

    @abstractmethod
    def equity_bars(self, start: datetime, end: datetime, tickers: list[str]) -> pd.DataFrame:
        """Return MultiIndex(timestamp, ticker) OHLCV data."""

    @abstractmethod
    def option_chain(self, underlying: str, timestamp: datetime) -> pd.DataFrame:
        """Return a timestamp-synchronized option-chain snapshot."""


class EventDataProvider(ABC):
    @abstractmethod
    def earnings_calendar(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Return point-in-time earnings-event metadata."""

    @abstractmethod
    def dividends(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Return known dividend forecasts and realized distributions."""

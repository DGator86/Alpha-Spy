from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OptionRight(str, Enum):
    CALL = "C"
    PUT = "P"


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    underlying: str
    timestamp: datetime
    expiration: datetime
    strike: float
    right: OptionRight
    bid: float
    ask: float
    underlying_price: float
    implied_volatility: float
    volume: int = 0
    open_interest: int = 0
    bid_size: int = 0
    ask_size: int = 0

    @property
    def midpoint(self) -> float:
        return max(0.0, (self.bid + self.ask) / 2.0)

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def relative_spread(self) -> float:
        return self.spread / max(self.midpoint, 0.01)

    @property
    def tenor_years(self) -> float:
        seconds = max((self.expiration - self.timestamp).total_seconds(), 0.0)
        return seconds / (365.0 * 24.0 * 3600.0)


@dataclass(frozen=True)
class EdgeEstimate:
    symbol: str
    structure: str
    model_value: float
    market_entry: float
    gross_edge: float
    execution_cost: float
    uncertainty: float
    net_edge: float
    probability_positive_edge: float
    max_profit: float
    max_loss: float
    expected_payoff_physical: float
    metadata: dict[str, float | str]

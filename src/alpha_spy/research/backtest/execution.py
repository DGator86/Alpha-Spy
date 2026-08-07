from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FillModel:
    fee_per_contract: float = 0.65
    slippage_fraction_of_spread: float = 0.25
    minimum_slippage: float = 0.01
    multiplier: int = 100

    def buy_price(self, bid: float, ask: float) -> float:
        spread = max(ask - bid, 0.0)
        return ask + max(self.minimum_slippage, self.slippage_fraction_of_spread * spread)

    def sell_price(self, bid: float, ask: float) -> float:
        spread = max(ask - bid, 0.0)
        return max(0.0, bid - max(self.minimum_slippage, self.slippage_fraction_of_spread * spread))

    def fees(self, contracts: int, legs: int = 1) -> float:
        return abs(contracts) * legs * self.fee_per_contract

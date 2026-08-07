from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ExpirationTrade:
    opened_at: pd.Timestamp
    expiration: pd.Timestamp
    structure: str
    right: str
    long_strike: float
    short_strike: float | None
    entry_debit: float
    contracts: int
    multiplier: int = 100

    def payoff(self, terminal_spot: float) -> float:
        if self.right.upper().startswith("C"):
            long_value = max(terminal_spot - self.long_strike, 0.0)
            short_value = 0.0 if self.short_strike is None else max(terminal_spot - self.short_strike, 0.0)
        else:
            long_value = max(self.long_strike - terminal_spot, 0.0)
            short_value = 0.0 if self.short_strike is None else max(self.short_strike - terminal_spot, 0.0)
        return (long_value - short_value - self.entry_debit) * self.contracts * self.multiplier


def summarize_expiration_backtest(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {
            "trades": 0.0,
            "net_pnl": 0.0,
            "win_rate": 0.0,
            "average_pnl": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }
    pnl = trades["pnl"].astype(float)
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax()
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    return {
        "trades": float(len(pnl)),
        "net_pnl": float(pnl.sum()),
        "win_rate": float((pnl > 0).mean()),
        "average_pnl": float(pnl.mean()),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf"),
        "max_drawdown": float(drawdown.min()),
        "pnl_std": float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0,
        "sharpe_per_trade": float(pnl.mean() / pnl.std(ddof=1)) if len(pnl) > 1 and pnl.std(ddof=1) > 0 else 0.0,
    }

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .covariance import CovarianceForecast


@dataclass(frozen=True)
class TerminalDistribution:
    returns: np.ndarray
    prices: np.ndarray
    spot: float
    horizon_years: float
    measure: str

    @property
    def expected_return(self) -> float:
        return float(np.mean(self.returns))

    @property
    def volatility(self) -> float:
        return float(np.std(self.returns, ddof=1))

    def probability_above(self, strike: float) -> float:
        return float(np.mean(self.prices > strike))

    def expected_payoff(self, strike: float, right: str) -> float:
        if right.upper().startswith("C"):
            payoff = np.maximum(self.prices - strike, 0.0)
        else:
            payoff = np.maximum(strike - self.prices, 0.0)
        return float(np.mean(payoff))


class PhysicalDistributionModel:
    def __init__(self, paths: int = 30_000, student_t_df: float = 7.0, seed: int = 86):
        self.paths = paths
        self.student_t_df = student_t_df
        self.rng = np.random.default_rng(seed)

    def simulate(
        self,
        *,
        spot: float,
        weights: pd.Series,
        mean_returns: pd.Series,
        covariance: CovarianceForecast,
        horizon_scale: float = 1.0,
    ) -> TerminalDistribution:
        tickers = covariance.covariance.columns
        w = weights.reindex(tickers).fillna(0.0).to_numpy(dtype=float)
        w = w / w.sum()
        mu = mean_returns.reindex(tickers).fillna(0.0).to_numpy(dtype=float) * horizon_scale
        cov = covariance.covariance.to_numpy(dtype=float) * horizon_scale
        chol = np.linalg.cholesky(cov)
        z = self.rng.standard_normal((self.paths, len(tickers)))
        if self.student_t_df > 2:
            chi = self.rng.chisquare(self.student_t_df, self.paths)
            z = z * np.sqrt((self.student_t_df - 2.0) / chi)[:, None]
        constituent_log_returns = mu + z @ chol.T
        constituent_simple_returns = np.expm1(constituent_log_returns)
        index_returns = constituent_simple_returns @ w
        prices = spot * (1.0 + index_returns)
        return TerminalDistribution(
            returns=index_returns,
            prices=prices,
            spot=spot,
            horizon_years=float(horizon_scale),
            measure="P",
        )

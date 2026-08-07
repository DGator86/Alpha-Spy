from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

from .physical import TerminalDistribution


@dataclass(frozen=True)
class SmileSlice:
    ticker: str
    spot: float
    tenor_years: float
    forward: float
    log_moneyness: np.ndarray
    implied_volatility: np.ndarray

    def vol(self, log_moneyness: np.ndarray | float) -> np.ndarray:
        x = np.asarray(log_moneyness, dtype=float)
        if len(self.log_moneyness) == 1:
            return np.full_like(x, self.implied_volatility[0], dtype=float)
        interpolator = PchipInterpolator(
            self.log_moneyness,
            self.implied_volatility,
            extrapolate=True,
        )
        values = interpolator(x)
        return np.clip(values, 0.01, 5.0)


def build_smile_slice(
    chain: pd.DataFrame,
    *,
    ticker: str,
    spot: float,
    tenor_years: float,
    rate: float,
    dividend_yield: float,
) -> SmileSlice:
    frame = chain.copy()
    frame = frame[(frame["implied_volatility"] > 0) & (frame["strike"] > 0)]
    if frame.empty:
        raise ValueError(f"No valid implied volatilities for {ticker}")
    grouped = frame.groupby("strike", as_index=False)["implied_volatility"].median()
    forward = spot * np.exp((rate - dividend_yield) * tenor_years)
    grouped["log_moneyness"] = np.log(grouped["strike"] / forward)
    grouped = grouped.sort_values("log_moneyness").drop_duplicates("log_moneyness")
    return SmileSlice(
        ticker=ticker,
        spot=float(spot),
        tenor_years=float(tenor_years),
        forward=float(forward),
        log_moneyness=grouped["log_moneyness"].to_numpy(dtype=float),
        implied_volatility=grouped["implied_volatility"].to_numpy(dtype=float),
    )


class SyntheticRiskNeutralModel:
    """Construct a synthetic index distribution from constituent smiles and independent dependence.

    The smile mapping is a pragmatic skew-aware transformation for relative-value research. It is not
    a replacement for a fully calibrated arbitrage-free local/stochastic-volatility model.
    """

    def __init__(self, paths: int = 30_000, seed: int = 86):
        self.paths = paths
        self.rng = np.random.default_rng(seed)

    def simulate(
        self,
        *,
        index_spot: float,
        weights: pd.Series,
        smiles: dict[str, SmileSlice],
        correlation: pd.DataFrame,
        rate: float,
        correlation_risk_premium: float = 0.0,
        downside_correlation_increment: float = 0.0,
    ) -> TerminalDistribution:
        tickers = [ticker for ticker in correlation.columns if ticker in smiles and ticker in weights.index]
        if len(tickers) < 2:
            raise ValueError("At least two constituent smiles are required")
        w = weights.reindex(tickers).fillna(0.0).astype(float)
        w = w / w.sum()
        # copy=True is required: under pandas copy-on-write (the default from
        # 3.0) to_numpy() can hand back a read-only view of the frame's block,
        # and the correlation-risk-premium write below mutates this array.
        corr = correlation.reindex(index=tickers, columns=tickers).to_numpy(dtype=float, copy=True)
        # Apply a non-circular, externally estimated normal correlation-risk premium.
        off_diag = ~np.eye(len(corr), dtype=bool)
        corr[off_diag] = np.clip(corr[off_diag] + correlation_risk_premium, -0.95, 0.99)
        np.fill_diagonal(corr, 1.0)
        eigval, eigvec = np.linalg.eigh((corr + corr.T) / 2.0)
        eigval = np.maximum(eigval, 1e-8)
        corr = (eigvec * eigval) @ eigvec.T
        d = np.sqrt(np.diag(corr))
        corr = corr / np.outer(d, d)
        chol = np.linalg.cholesky(corr)
        base_z = self.rng.standard_normal((self.paths, len(tickers))) @ chol.T

        tenor = float(np.median([smiles[t].tenor_years for t in tickers]))
        constituent_simple = np.zeros_like(base_z)
        for j, ticker in enumerate(tickers):
            smile = smiles[ticker]
            z = base_z[:, j]
            # Downside-specific common-state adjustment approximates asymmetric dependence.
            if downside_correlation_increment != 0:
                common_down = np.minimum(base_z.mean(axis=1), 0.0)
                z = z + downside_correlation_increment * common_down
            # Estimate strike region from standardized terminal state, then query that smile region.
            atm_vol = float(smile.vol(0.0))
            provisional_log_return = -0.5 * atm_vol**2 * tenor + atm_vol * np.sqrt(tenor) * z
            local_vol = smile.vol(provisional_log_return)
            log_return = (
                (rate - 0.5 * local_vol**2) * tenor
                + local_vol * np.sqrt(tenor) * z
            )
            constituent_simple[:, j] = np.expm1(log_return)

        index_returns = constituent_simple @ w.to_numpy(dtype=float)
        prices = index_spot * (1.0 + index_returns)
        return TerminalDistribution(
            returns=index_returns,
            prices=prices,
            spot=index_spot,
            horizon_years=tenor,
            measure="Q",
        )

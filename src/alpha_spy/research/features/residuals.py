from __future__ import annotations

import numpy as np
import pandas as pd


def estimate_rolling_betas(
    asset_returns: pd.DataFrame,
    market_return: pd.Series,
    sector_returns: pd.DataFrame | None = None,
    ticker_to_sector: dict[str, str] | None = None,
    *,
    window: int = 390,
    min_periods: int = 100,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Estimate trailing market and sector betas without using future observations."""
    mkt_var = market_return.rolling(window, min_periods=min_periods).var().replace(0.0, np.nan)
    market_beta = pd.DataFrame(index=asset_returns.index, columns=asset_returns.columns, dtype=float)
    sector_beta = pd.DataFrame(0.0, index=asset_returns.index, columns=asset_returns.columns)

    for ticker in asset_returns.columns:
        market_beta[ticker] = (
            asset_returns[ticker].rolling(window, min_periods=min_periods).cov(market_return) / mkt_var
        )
        if sector_returns is not None and ticker_to_sector is not None:
            sector = ticker_to_sector.get(ticker)
            if sector in sector_returns.columns:
                sr = sector_returns[sector]
                sr_var = sr.rolling(window, min_periods=min_periods).var().replace(0.0, np.nan)
                sector_beta[ticker] = (
                    asset_returns[ticker].rolling(window, min_periods=min_periods).cov(sr) / sr_var
                )
    return market_beta.shift(1), sector_beta.shift(1)


def residualize_returns(
    asset_returns: pd.DataFrame,
    market_return: pd.Series,
    market_betas: pd.DataFrame,
    sector_returns: pd.DataFrame | None = None,
    sector_betas: pd.DataFrame | None = None,
    ticker_to_sector: dict[str, str] | None = None,
) -> pd.DataFrame:
    explained = market_betas.mul(market_return, axis=0)
    if sector_returns is not None and sector_betas is not None and ticker_to_sector is not None:
        for ticker in asset_returns.columns:
            sector = ticker_to_sector.get(ticker)
            if sector in sector_returns.columns:
                explained[ticker] = explained[ticker] + sector_betas[ticker] * sector_returns[sector]
    residuals = asset_returns - explained
    return residuals.replace([np.inf, -np.inf], np.nan)

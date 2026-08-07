from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CovarianceForecast:
    covariance: pd.DataFrame
    correlation: pd.DataFrame
    volatilities: pd.Series
    effective_observations: int
    regime: str


def _nearest_psd(matrix: np.ndarray, minimum_eigenvalue: float = 1e-8) -> np.ndarray:
    symmetric = (matrix + matrix.T) / 2.0
    values, vectors = np.linalg.eigh(symmetric)
    values = np.maximum(values, minimum_eigenvalue)
    psd = (vectors * values) @ vectors.T
    return (psd + psd.T) / 2.0


def _ewma_covariance(data: pd.DataFrame, halflife: float) -> np.ndarray:
    clean = data.dropna(how="all")
    if len(clean) < 3:
        raise ValueError("At least three observations are required for covariance estimation")
    x = clean.fillna(0.0).to_numpy(dtype=float)
    ages = np.arange(len(x) - 1, -1, -1)
    weights = 0.5 ** (ages / max(halflife, 1.0))
    weights = weights / weights.sum()
    mean = np.average(x, axis=0, weights=weights)
    centered = x - mean
    return (centered * weights[:, None]).T @ centered


class DynamicCovarianceModel:
    def __init__(
        self,
        *,
        halflife: float = 390,
        shrinkage: float = 0.35,
        minimum_eigenvalue: float = 1e-8,
    ):
        if not 0 <= shrinkage <= 1:
            raise ValueError("shrinkage must be between 0 and 1")
        self.halflife = halflife
        self.shrinkage = shrinkage
        self.minimum_eigenvalue = minimum_eigenvalue

    def fit(
        self,
        returns: pd.DataFrame,
        *,
        downside: bool = False,
        market_return: pd.Series | None = None,
    ) -> CovarianceForecast:
        data = returns.copy()
        regime = "unconditional"
        if downside:
            if market_return is None:
                market_return = returns.mean(axis=1)
            mask = market_return.reindex(data.index) < 0
            selected = data.loc[mask]
            if len(selected.dropna(how="all")) >= max(20, data.shape[1] // 3):
                data = selected
                regime = "downside"

        empirical = _ewma_covariance(data, self.halflife)
        variances = np.maximum(np.diag(empirical), self.minimum_eigenvalue)
        constant_corr = np.eye(len(variances))
        std = np.sqrt(variances)
        denom = np.outer(std, std)
        corr = np.divide(empirical, denom, out=np.eye(len(std)), where=denom > 0)
        off_diag = corr[~np.eye(len(corr), dtype=bool)]
        avg_corr = float(np.nanmedian(off_diag)) if off_diag.size else 0.0
        constant_corr[:] = avg_corr
        np.fill_diagonal(constant_corr, 1.0)
        target = constant_corr * denom
        shrunk = (1.0 - self.shrinkage) * empirical + self.shrinkage * target
        shrunk = _nearest_psd(shrunk, self.minimum_eigenvalue)
        vol = np.sqrt(np.diag(shrunk))
        correlation = shrunk / np.outer(vol, vol)
        np.fill_diagonal(correlation, 1.0)
        columns = data.columns
        return CovarianceForecast(
            covariance=pd.DataFrame(shrunk, index=columns, columns=columns),
            correlation=pd.DataFrame(correlation, index=columns, columns=columns),
            volatilities=pd.Series(vol, index=columns),
            effective_observations=int(data.dropna(how="all").shape[0]),
            regime=regime,
        )


def portfolio_variance(weights: pd.Series, covariance: pd.DataFrame) -> float:
    w = weights.reindex(covariance.columns).fillna(0.0).to_numpy(dtype=float)
    if w.sum() <= 0:
        raise ValueError("Portfolio weights have no positive overlap with covariance matrix")
    w = w / w.sum()
    return float(w @ covariance.to_numpy(dtype=float) @ w)


def implied_equicorrelation(
    index_variance: float,
    constituent_vols: pd.Series,
    weights: pd.Series,
) -> float:
    sigma = constituent_vols.astype(float)
    w = weights.reindex(sigma.index).fillna(0.0).astype(float)
    w = w / w.sum()
    diagonal = float(np.sum((w.to_numpy() ** 2) * (sigma.to_numpy() ** 2)))
    weighted_vol = w.to_numpy() * sigma.to_numpy()
    cross = float(weighted_vol.sum() ** 2 - np.sum(weighted_vol**2))
    if cross <= 0:
        return 0.0
    return float((index_variance - diagonal) / cross)

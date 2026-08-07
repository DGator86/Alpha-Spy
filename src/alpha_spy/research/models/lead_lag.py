from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge


@dataclass
class LeadLagFit:
    model: Ridge
    feature_columns: list[str]
    target_horizon: int
    train_r2: float


class DistributedLagForecaster:
    """Regularized, dynamic constituent-to-SPY response model.

    Input features should already be residualized against contemporaneous ES/market and sector moves.
    """

    def __init__(self, lags: list[int], alpha: float = 10.0):
        if any(lag < 0 for lag in lags):
            raise ValueError("Lags must be non-negative")
        self.lags = sorted(set(lags))
        self.alpha = alpha
        self.fit_: LeadLagFit | None = None

    def _design(
        self,
        residual_returns: pd.DataFrame,
        weights: pd.Series,
        context: pd.DataFrame | None,
    ) -> pd.DataFrame:
        w = weights.reindex(residual_returns.columns).fillna(0.0)
        if w.sum() <= 0:
            raise ValueError("No positive constituent weight coverage")
        w = w / w.sum()
        aggregate = residual_returns.mul(w, axis=1).sum(axis=1, min_count=1)
        data: dict[str, pd.Series] = {}
        for lag in self.lags:
            data[f"weighted_residual_lag_{lag}"] = aggregate.shift(lag)
        if context is not None:
            for column in context.columns:
                data[f"context_{column}"] = context[column]
        return pd.DataFrame(data, index=residual_returns.index)

    def fit(
        self,
        residual_returns: pd.DataFrame,
        weights: pd.Series,
        spy_returns: pd.Series,
        *,
        horizon: int = 1,
        context: pd.DataFrame | None = None,
        sample_weight: pd.Series | None = None,
    ) -> LeadLagFit:
        X = self._design(residual_returns, weights, context)
        y = spy_returns.shift(-horizon)
        joined = X.join(y.rename("target")).dropna()
        if len(joined) < max(50, 3 * joined.shape[1]):
            raise ValueError("Insufficient observations for distributed-lag fit")
        weights_arr = None
        if sample_weight is not None:
            weights_arr = sample_weight.reindex(joined.index).fillna(0.0).to_numpy()
        model = Ridge(alpha=self.alpha, fit_intercept=True)
        model.fit(joined[X.columns], joined["target"], sample_weight=weights_arr)
        fit = LeadLagFit(
            model=model,
            feature_columns=list(X.columns),
            target_horizon=horizon,
            train_r2=float(model.score(joined[X.columns], joined["target"])),
        )
        self.fit_ = fit
        return fit

    def predict(
        self,
        residual_returns: pd.DataFrame,
        weights: pd.Series,
        *,
        context: pd.DataFrame | None = None,
    ) -> pd.Series:
        if self.fit_ is None:
            raise RuntimeError("Forecaster has not been fitted")
        X = self._design(residual_returns, weights, context)
        X = X.reindex(columns=self.fit_.feature_columns)
        valid = X.notna().all(axis=1)
        out = pd.Series(np.nan, index=X.index, name="predicted_spy_return")
        out.loc[valid] = self.fit_.model.predict(X.loc[valid])
        return out


def cross_correlation_profile(
    leader: pd.Series,
    follower: pd.Series,
    max_lag: int,
) -> pd.Series:
    """Correlation of leader(t) with follower(t+lag); positive lag means leader leads."""
    values = {}
    for lag in range(-max_lag, max_lag + 1):
        values[lag] = leader.corr(follower.shift(-lag))
    return pd.Series(values, name="cross_correlation")

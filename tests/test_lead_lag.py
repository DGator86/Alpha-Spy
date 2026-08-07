import numpy as np
import pandas as pd

from spy_constituent_alpha.models.lead_lag import DistributedLagForecaster, cross_correlation_profile


def test_detects_positive_lead():
    rng = np.random.default_rng(4)
    leader = pd.Series(rng.normal(size=3000))
    follower = 0.8 * leader.shift(3) + pd.Series(rng.normal(scale=0.2, size=3000))
    profile = cross_correlation_profile(leader, follower, max_lag=8)
    assert profile.idxmax() == 3


def test_distributed_lag_forecaster_runs():
    rng = np.random.default_rng(5)
    residuals = pd.DataFrame(rng.normal(size=(2000, 3)), columns=list("ABC"))
    weights = pd.Series([0.5, 0.3, 0.2], index=list("ABC"))
    aggregate = residuals.mul(weights, axis=1).sum(axis=1)
    spy = 0.7 * aggregate.shift(1) + pd.Series(rng.normal(scale=0.2, size=2000))
    model = DistributedLagForecaster([0, 1, 2], alpha=1.0)
    fit = model.fit(residuals, weights, spy, horizon=1)
    prediction = model.predict(residuals, weights)
    assert fit.train_r2 > 0.4
    assert prediction.notna().sum() > 1900

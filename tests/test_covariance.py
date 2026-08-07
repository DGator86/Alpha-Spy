import numpy as np
import pandas as pd

from spy_constituent_alpha.models.covariance import (
    DynamicCovarianceModel,
    implied_equicorrelation,
)


def test_covariance_is_psd():
    rng = np.random.default_rng(1)
    data = pd.DataFrame(rng.normal(size=(1000, 8)) * 0.01, columns=list("ABCDEFGH"))
    forecast = DynamicCovarianceModel(shrinkage=0.3).fit(data)
    eig = np.linalg.eigvalsh(forecast.covariance.to_numpy())
    assert eig.min() > 0


def test_implied_equicorrelation_identity():
    vols = pd.Series([0.2, 0.3, 0.25], index=list("ABC"))
    weights = pd.Series([0.5, 0.3, 0.2], index=list("ABC"))
    rho = 0.4
    cov = np.outer(vols, vols) * rho
    np.fill_diagonal(cov, vols.to_numpy() ** 2)
    variance = float(weights.to_numpy() @ cov @ weights.to_numpy())
    recovered = implied_equicorrelation(variance, vols, weights)
    assert abs(recovered - rho) < 1e-10

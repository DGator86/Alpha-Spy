import numpy as np
import pandas as pd
import pytest

from alpha_spy.research.models.risk_neutral import (
    SmileSlice,
    SyntheticRiskNeutralModel,
    build_smile_slice,
)

TICKERS = ["AAA", "BBB", "CCC"]


def _smiles() -> dict[str, SmileSlice]:
    return {
        ticker: SmileSlice(
            ticker=ticker,
            spot=100.0,
            tenor_years=0.02,
            forward=100.1,
            log_moneyness=np.array([-0.05, 0.0, 0.05]),
            implied_volatility=np.array([0.24, 0.20, 0.22]),
        )
        for ticker in TICKERS
    }


def _correlation(rho: float = 0.35) -> pd.DataFrame:
    corr = np.full((len(TICKERS), len(TICKERS)), rho)
    np.fill_diagonal(corr, 1.0)
    return pd.DataFrame(corr, index=TICKERS, columns=TICKERS)


def _weights() -> pd.Series:
    return pd.Series([0.5, 0.3, 0.2], index=TICKERS)


@pytest.mark.parametrize("premium", [0.0, 0.10, -0.05])
def test_simulate_does_not_write_through_the_correlation_frame(premium):
    """The correlation-risk premium is applied to a copy, not the caller's frame.

    pandas returns a read-only view from to_numpy() under copy-on-write, so
    without an explicit copy this raises "assignment destination is read-only".
    """
    correlation = _correlation()
    before = correlation.to_numpy(copy=True)

    distribution = SyntheticRiskNeutralModel(paths=2000, seed=11).simulate(
        index_spot=100.0,
        weights=_weights(),
        smiles=_smiles(),
        correlation=correlation,
        rate=0.045,
        correlation_risk_premium=premium,
    )

    assert distribution.returns.size == 2000
    assert np.isfinite(distribution.returns).all()
    # The caller's frame must be untouched.
    np.testing.assert_array_equal(correlation.to_numpy(), before)


def test_simulate_survives_a_read_only_correlation_frame():
    """Explicitly pin the regression: a frame backed by a read-only array."""
    corr = _correlation()
    block = corr.to_numpy(copy=True)
    block.flags.writeable = False
    read_only = pd.DataFrame(block, index=TICKERS, columns=TICKERS)
    assert not read_only.to_numpy().flags.writeable

    distribution = SyntheticRiskNeutralModel(paths=1000, seed=5).simulate(
        index_spot=100.0,
        weights=_weights(),
        smiles=_smiles(),
        correlation=read_only,
        rate=0.045,
        correlation_risk_premium=0.08,
    )
    assert np.isfinite(distribution.returns).all()


def test_simulate_requires_at_least_two_smiles():
    with pytest.raises(ValueError, match="At least two"):
        SyntheticRiskNeutralModel(paths=100).simulate(
            index_spot=100.0,
            weights=_weights(),
            smiles={"AAA": _smiles()["AAA"]},
            correlation=_correlation(),
            rate=0.045,
        )


def test_build_smile_slice_orders_by_log_moneyness():
    chain = pd.DataFrame(
        {
            "strike": [105.0, 95.0, 100.0],
            "implied_volatility": [0.22, 0.26, 0.20],
        }
    )
    smile = build_smile_slice(
        chain, ticker="AAA", spot=100.0, tenor_years=0.02, rate=0.045, dividend_yield=0.012
    )
    assert np.all(np.diff(smile.log_moneyness) > 0)
    assert smile.implied_volatility.size == 3

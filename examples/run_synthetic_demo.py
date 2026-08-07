from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spy_constituent_alpha.models.covariance import DynamicCovarianceModel
from spy_constituent_alpha.models.physical import PhysicalDistributionModel
from spy_constituent_alpha.models.risk_neutral import (
    SyntheticRiskNeutralModel,
    build_smile_slice,
)
from spy_constituent_alpha.pricing.black_scholes import black_scholes_price
from spy_constituent_alpha.signals.mispricing import ScanSettings, scan_long_options, scan_vertical_spreads
from spy_constituent_alpha.signals.ranking import edges_to_frame
from spy_constituent_alpha.signals.structures import (
    scan_butterflies,
    scan_credit_verticals,
    scan_iron_condors,
)


def make_constituents(seed: int = 86):
    rng = np.random.default_rng(seed)
    tickers = [f"S{i:02d}" for i in range(20)]
    raw_weights = rng.lognormal(mean=0.0, sigma=0.9, size=len(tickers))
    weights = pd.Series(raw_weights / raw_weights.sum(), index=tickers)
    sectors = {ticker: f"SEC{idx % 5}" for idx, ticker in enumerate(tickers)}
    rows = 1800
    market = rng.normal(0, 0.00065, rows)
    sector_factors = rng.normal(0, 0.00045, (rows, 5))
    returns = pd.DataFrame(index=pd.RangeIndex(rows), columns=tickers, dtype=float)
    for idx, ticker in enumerate(tickers):
        beta = rng.uniform(0.7, 1.35)
        idio = rng.normal(0, rng.uniform(0.00045, 0.0012), rows)
        returns[ticker] = beta * market + 0.55 * sector_factors[:, idx % 5] + idio
    return tickers, weights, sectors, returns


def make_option_chain(
    tickers: list[str],
    spots: pd.Series,
    now: datetime,
    expiration: datetime,
    rate: float,
    dividend_yield: float,
):
    tenor = (expiration - now).total_seconds() / (365 * 24 * 3600)
    constituent_chains: dict[str, pd.DataFrame] = {}
    for idx, ticker in enumerate(tickers):
        spot = spots[ticker]
        base_vol = 0.20 + 0.035 * (idx % 5) + 0.02 * (idx / len(tickers))
        strikes = np.linspace(0.85 * spot, 1.15 * spot, 13)
        rows = []
        for strike in strikes:
            k = np.log(strike / spot)
            vol = base_vol - 0.28 * k + 0.55 * k**2
            rows.append({"strike": strike, "implied_volatility": vol})
        constituent_chains[ticker] = pd.DataFrame(rows)
    return constituent_chains, tenor


def make_spy_chain(
    spot: float,
    now: datetime,
    expiration: datetime,
    risk_neutral_prices,
    rate: float,
):
    rows = []
    strikes = np.arange(round(spot * 0.94), round(spot * 1.06) + 1, 2.0)
    for right in ("C", "P"):
        for strike in strikes:
            theoretical = np.exp(-rate * risk_neutral_prices.horizon_years) * risk_neutral_prices.expected_payoff(strike, right)
            # Introduce deliberate underpricing near 497 calls and 493 puts.
            discount = 0.0
            if right == "C" and abs(strike - 498) <= 2:
                discount = 0.32
            if right == "P" and abs(strike - 492) <= 2:
                discount = 0.24
            mid = max(0.03, theoretical - discount)
            spread = max(0.04, 0.04 * mid)
            bid = max(0.01, mid - spread / 2)
            ask = mid + spread / 2
            rows.append(
                {
                    "symbol": f"SPY_{expiration:%Y%m%d}_{right}_{strike:.0f}",
                    "underlying": "SPY",
                    "timestamp": now,
                    "expiration": expiration,
                    "strike": float(strike),
                    "right": right,
                    "bid": bid,
                    "ask": ask,
                    "underlying_price": spot,
                    "implied_volatility": 0.18,
                    "volume": 500,
                    "open_interest": 5000,
                }
            )
    return pd.DataFrame(rows)


def main():
    tickers, weights, sectors, returns = make_constituents()
    covariance_model = DynamicCovarianceModel(halflife=390, shrinkage=0.25)
    covariance = covariance_model.fit(returns.tail(1200))
    downside_covariance = covariance_model.fit(
        returns.tail(1200), downside=True, market_return=returns.mean(axis=1).tail(1200)
    )

    now = datetime(2026, 8, 4, 14, 30, tzinfo=timezone.utc)
    expiration = now + timedelta(days=3)
    rate = 0.045
    dividend_yield = 0.012
    spots = pd.Series({ticker: 80 + 4 * idx for idx, ticker in enumerate(tickers)})
    constituent_chains, tenor = make_option_chain(
        tickers, spots, now, expiration, rate, dividend_yield
    )
    smiles = {
        ticker: build_smile_slice(
            constituent_chains[ticker],
            ticker=ticker,
            spot=spots[ticker],
            tenor_years=tenor,
            rate=rate,
            dividend_yield=dividend_yield,
        )
        for ticker in tickers
    }

    spy_spot = 495.0
    q_model = SyntheticRiskNeutralModel(paths=80_000, seed=86)
    q_distribution = q_model.simulate(
        index_spot=spy_spot,
        weights=weights,
        smiles=smiles,
        correlation=covariance.correlation,
        rate=rate,
        correlation_risk_premium=0.045,
        downside_correlation_increment=0.12,
    )

    physical_model = PhysicalDistributionModel(paths=80_000, student_t_df=7, seed=87)
    forecast_mean = pd.Series(0.0, index=tickers)
    # Broad positive pressure is embedded in the physical distribution.
    forecast_mean += weights.rank(pct=True) * 0.00035
    p_distribution = physical_model.simulate(
        spot=spy_spot,
        weights=weights,
        mean_returns=forecast_mean,
        covariance=downside_covariance,
        horizon_scale=3.0,
    )

    chain = make_spy_chain(spy_spot, now, expiration, q_distribution, rate)
    settings = ScanSettings(
        min_net_edge=0.04,
        min_edge_to_uncertainty=1.10,
        min_probability_positive_edge=0.60,
        max_relative_spread=0.35,
        max_quote_age_ms=1000,
    )
    longs = scan_long_options(
        chain,
        risk_neutral=q_distribution,
        physical=p_distribution,
        rate=rate,
        model_uncertainty=0.08,
        settings=settings,
        now=now,
    )
    verticals = scan_vertical_spreads(
        chain,
        risk_neutral=q_distribution,
        physical=p_distribution,
        rate=rate,
        model_uncertainty=0.06,
        settings=settings,
        now=now,
        max_width=8,
    )
    credits = scan_credit_verticals(
        chain,
        risk_neutral=q_distribution,
        physical=p_distribution,
        rate=rate,
        model_uncertainty=0.06,
        settings=settings,
        now=now,
        max_width=8,
    )
    butterflies = scan_butterflies(
        chain,
        risk_neutral=q_distribution,
        physical=p_distribution,
        rate=rate,
        model_uncertainty=0.05,
        settings=settings,
        now=now,
        max_wing_width=6,
    )
    condors = scan_iron_condors(
        chain,
        risk_neutral=q_distribution,
        physical=p_distribution,
        rate=rate,
        model_uncertainty=0.05,
        settings=settings,
        now=now,
        max_wing_width=4,
        min_body_width=4,
    )
    output = edges_to_frame(
        longs[:15] + verticals[:25] + credits[:15] + butterflies[:15] + condors[:15]
    )
    out_path = ROOT / "examples" / "synthetic_edge_output.csv"
    output.to_csv(out_path, index=False)

    print("Synthetic constituent-implied SPY scanner")
    print(f"Constituents: {len(tickers)} | covered weight: {weights.sum():.2%}")
    print(f"Q expected return: {q_distribution.expected_return:.4%}")
    print(f"P expected return: {p_distribution.expected_return:.4%}")
    print(f"Candidates: {len(output)}")
    if output.empty:
        print("No candidates passed thresholds.")
    else:
        columns = [
            "symbol",
            "structure",
            "net_edge",
            "edge_to_uncertainty",
            "probability_positive_edge",
            "max_loss",
        ]
        print(output[columns].head(15).to_string(index=False))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()

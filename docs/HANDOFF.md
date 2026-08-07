# Engineering Handoff

## What is implemented

- Vendor-neutral market-data interfaces and a CSV/Parquet research adapter.
- Backward-only timestamp synchronization with quote-age rejection.
- Weighted breadth, concentration, dispersion, and coverage features.
- Rolling market/sector residualization.
- Distributed-lag SPY return forecaster.
- Shrunk EWMA covariance with positive-semidefinite repair and downside regime support.
- Physical terminal-distribution simulation using fat-tailed constituent shocks.
- Constituent option-smile interpolation and a skew-aware synthetic risk-neutral SPY distribution.
- Independent correlation-risk-premium estimator.
- Incomplete constituent-option coverage completion with an explicit uncertainty penalty.
- Black-Scholes, implied volatility, and American binomial pricing.
- Cost-adjusted scanners for long options, debit spreads, credit spreads, butterflies, and iron condors.
- Finite maximum-loss enforcement for every short-premium structure.
- Shapley edge attribution helper.
- Expiration backtest primitives and probability-calibration metrics.
- Synthetic end-to-end demonstration and automated tests.

## What is deliberately not faked

The repository does not pretend to be production-ready without real point-in-time data. The following require vendor credentials, exchange symbology, and historical datasets:

- Massive/Polygon, Databento, Cboe, ORATS, ThetaData, or other live/historical adapters.
- Historical S&P Dow Jones point-in-time membership and float-adjusted weights.
- Full discrete-dividend and borrow histories.
- Live order routing and brokerage-specific complex-order handling.
- Production-grade arbitrage-free volatility-surface calibration.
- A validated historical correlation-risk-premium dataset.
- Dealer gamma and market-depth feeds.

## First production milestone

Build a **basket integrity harness** before training any alpha model.

For each synchronized timestamp:

1. Load point-in-time constituents and weights.
2. Calculate the weighted basket return.
3. Compare the basket with SPY, SPX, and ES.
4. Decompose the residual into dividends, basis, stale constituents, halts, and data errors.
5. Store the residual and synchronization-quality score.

Do not continue until unexplained residuals are consistently smaller than the proposed option edge.

## Recommended implementation order

1. Implement the chosen equity/options data adapter.
2. Implement point-in-time index membership and corporate actions.
3. Create immutable synchronized snapshot files.
4. Reproduce basket/SPY/SPX/ES relationships.
5. Train the price/breadth model.
6. Train the physical variance and tail model.
7. Construct cleaned constituent option surfaces.
8. Estimate the correlation-risk premium independently.
9. Run walk-forward option valuation and structure selection.
10. Operate a live shadow scanner before risking capital.

## Required data partitions

Use calendar-based, non-overlapping partitions:

- Train: expanding or fixed trailing window.
- Validation: parameter and threshold selection.
- Test: untouched period.
- Live shadow: immutable forward observations.

All feature selection, lag selection, regime boundaries, and uncertainty calibration must occur inside the walk-forward loop.

## Promotion gates

A model cannot advance unless it passes all gates:

- Data integrity and timestamp audit.
- Probability calibration against baseline.
- Executable P&L after ask/bid fills.
- Doubled-cost stress test.
- Regime and year stability.
- No excessive overlap among positions.
- Maximum-loss and portfolio-tail limits.
- Shadow-live agreement with backtest assumptions.

## Suggested repository issue sequence

1. `DATA-001` Point-in-time S&P 500 universe loader.
2. `DATA-002` Synchronized equity/option/future snapshot builder.
3. `QA-001` Basket reconstruction and basis attribution report.
4. `MODEL-001` ES/sector residualized breadth forecast.
5. `MODEL-002` Dynamic factor covariance and downside dependence.
6. `SURFACE-001` Bid/ask constituent total-variance surfaces.
7. `PREMIUM-001` Correlation-risk-premium history and regime model.
8. `BACKTEST-001` Walk-forward contract and structure ledger.
9. `EXEC-001` Complex-order fill and cancellation simulator.
10. `LIVE-001` Read-only real-time shadow scanner.

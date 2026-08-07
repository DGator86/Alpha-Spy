# Research Roadmap

## Phase 0 — Integrity

- Point-in-time universe and weights.
- Timestamp and stale-quote audit.
- Corporate action, dividend, and expiration handling.
- Reproduce SPY from the constituent basket and explain basis residuals.

Exit test: basket reconstruction error is stable, explainable, and smaller than candidate option edges.

## Phase 1 — Price and breadth model

- Distributed-lag response kernels by horizon and regime.
- Breadth, concentration, sector confirmation, equal-weight divergence.
- ES and sector residualization.

Exit test: statistically and economically significant out-of-sample improvement over SPY/ES-only baseline.

## Phase 2 — Physical variance and tails

- Realized variance, bipower variation, jumps, overnight effects.
- Dynamic factor covariance and downside dependence.
- Terminal-distribution calibration.

Exit test: improved CRPS, log score, Brier score, and realized-variance forecast.

## Phase 3 — Constituent option surface

- Bid/ask implied surfaces.
- Earnings and borrow controls.
- Risk-neutral marginal distributions.
- Market/sector option factor residualization.

Exit test: stable synthetic variance and skew estimates with no static-arbitrage explosions.

## Phase 4 — Correlation risk premium

- Implied-versus-realized correlation history.
- Regime-conditioned premium.
- Separate downside and upside premium.
- Validate against Cboe COR, VIXEQ, and DSPX series.

Exit test: residual SPY surface discrepancy predicts subsequent relative option returns.

## Phase 5 — Defined-risk optimizer

- Long options, verticals, butterflies, iron condors.
- Fill probability and market-depth constraints.
- Portfolio-level overlap and tail controls.

Exit test: positive walk-forward net P&L under doubled-cost stress.

## Phase 6 — Shadow live system

- Live scanner with immutable decision logs.
- Capture full contemporaneous chain and feature state.
- No trading for at least one statistically adequate regime sample.
- Compare predicted fill with actual quote path.

Exit test: shadow performance agrees with backtest within defined tolerance.

# System Design

## 1. Objective

Estimate a complete SPY terminal-price distribution from constituent state, then test whether executable SPY option prices are inconsistent with that distribution after normal risk premia and trading costs.

The system must distinguish:

- **P-measure model:** expected actual outcomes and expected trade P&L.
- **Q-measure model:** no-arbitrage-compatible option valuation and surface comparison.

A contract may be cheap under a physical forecast but not under the risk-neutral surface, or vice versa. Both views are required.

## 2. Live pipeline

```text
Point-in-time weights + corporate actions
                    |
Constituent equities/options + SPY/SPX + ES + sectors
                    |
        Timestamp synchronization / quote QC
                    |
        +-----------+------------+
        |                        |
Physical feature engine     Risk-neutral surface engine
        |                        |
Return / variance / tail    Marginal smiles / term structures
forecast                    / borrow / dividends / events
        |                        |
        +------ dynamic asymmetric dependence ------+
                               |
                 Synthetic SPY terminal distribution
                               |
                     SPY market option chain
                               |
                  Cost- and uncertainty-adjusted edge
                               |
                    Defined-risk structure optimizer
                               |
                   Walk-forward and live shadow ledger
```

## 3. Regimes

Minimum regime state:

- Volatility: low, normal, high, crisis.
- Correlation: falling, stable, rising, dislocated.
- Breadth: broad, mixed, concentrated.
- Dealer state proxy: positive gamma, neutral, negative gamma.
- Session: opening, midday, final hour, expiration afternoon.
- Event: ordinary, earnings-heavy, macro announcement, rebalance.

Every prediction and risk-premium estimate should be conditioned on regime. Fixed coefficients are prohibited in production.

## 4. Constituent coverage

Two paths:

- **Full path:** all constituents with valid price data; all sufficiently liquid option surfaces.
- **Fast path:** top 50–100 constituents, sector completion model, and explicit uncovered-weight uncertainty penalty.

The system must report covered market-cap weight. It must not silently renormalize 60% option coverage to 100% without an uncertainty charge and completion model.

## 5. Dependence model

Production progression:

1. Shrunk EWMA covariance baseline.
2. Market/sector/idio factor covariance.
3. Dynamic conditional correlation.
4. Separate upside/downside correlation.
5. Joint-tail or copula model.
6. Independently estimated correlation-risk premium.

The current package supplies stages 1 and a practical downside overlay. Later stages plug into the same interface.

## 6. Option-surface construction

Required controls:

- Remove crossed, locked, stale, zero-bid, impossible-IV, and parity-violating points.
- Fit total variance, not raw IV, across tenor.
- Enforce calendar monotonicity and strike convexity where possible.
- Correct for discrete dividends and American exercise.
- Mark earnings-event variance separately.
- Retain bid and ask surfaces rather than a midpoint-only surface.

## 7. Edge decomposition

For each contract or structure:

```text
Raw model edge
- half/full spread appropriate to direction
- expected slippage
- commissions and exchange fees
- early-exercise / dividend uncertainty
- quote-age penalty
- uncovered-weight penalty
- model standard error
- regime-transition penalty
= executable net edge
```

The ranked output should also attribute the edge to:

- Direction
- Variance
- Correlation
- Skew
- Tail dependence
- Breadth/concentration
- Event premium

## 8. Allowed structures

Initial production scope:

- Long calls and puts
- Call and put debit spreads
- Call and put credit spreads with finite width
- Butterflies
- Iron condors
- Fully protected ratio structures

Prohibited:

- Naked short calls
- Naked short puts
- Stock-ownership-dependent covered calls
- Any structure whose worst-case loss cannot be computed before entry

## 9. Acceptance criteria

A strategy is not promotable unless it:

- Beats SPY surface-only, ES-only, and simple historical-volatility baselines.
- Remains profitable using ask-to-enter / bid-to-exit assumptions.
- Survives timestamp latency and quote cancellation simulation.
- Has acceptable probability calibration.
- Is profitable across multiple non-overlapping market regimes.
- Does not derive most P&L from one crisis or one calendar year.
- Retains edge after doubling modeled costs.
- Has no point-in-time membership, dividend, or earnings leakage.

## 10. Edge attribution

Every candidate should be repriced under counterfactual models with direction, variance, correlation,
skew, and tail overlays removed one at a time. The package includes a Shapley-value attribution helper so
interacting model effects add back to the total valuation residual rather than being double counted.

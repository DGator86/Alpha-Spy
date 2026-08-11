# Alpha-SPY v3.0 System Design

## Objective

Alpha-SPY is a paper-validation trading system for SPY options. It consumes real-time production market data, forms point-in-time multi-horizon physical (P) and risk-neutral (Q) forecasts, chooses only defined-risk structures with positive executable utility after costs and uncertainty, and sends orders only to a broker sandbox while the candidate is under validation.

A successful paper campaign may produce `ELIGIBLE_FOR_MANUAL_LIVE_REVIEW`. It never enables real-money trading automatically.

## Authoritative runtime pipeline

```text
Tradier production real-time REST/WebSocket
        + point-in-time SPY constituent universe
        + SPY/constituent option surfaces
        + cash/ETF context proxies
        + validated event calendar
                    |
                    v
      synchronized one-minute market state
                    |
        +-----------+------------+
        |                        |
 deterministic features      input-health/QC
        |                        |
        +-----------+------------+
                    |
       hierarchical regime classifier
 micro / intraday / swing / structural
                    |
        +-----------+------------+
        |                        |
 physical P distribution     risk-neutral Q distribution
 dynamic covariance          IV smiles + correlation premium
        |                        |
        +-----------+------------+
                    |
  multi-horizon forecasts and path distributions
5m / 15m / 30m / 60m / 120m / EOD / 1D / 5D
                    |
     P/Q mispricing + executable utility
                    |
       defined-risk strategy optimizer
                    |
 hard risk / data / uncertainty / broker vetoes
                    |
          NO_TRADE or 15m trade decision
                    |
         five-minute entry opportunity grid
                    |
          Tradier SANDBOX virtual account
                    |
 one-minute professional position management
                    |
 actual-fill settlement / confirmation tape
                    |
 replay + Dojo + promotion validation evidence
```

## Data-source truthfulness

Alpha-SPY never relabels a proxy as the underlying institutional feed.

- ES context: SPY cash proxy unless a direct futures source is later installed.
- NQ context: QQQ cash proxy.
- RTY context: IWM cash proxy.
- Credit: HYG proxy.
- Dollar: UUP proxy.
- Rates/curve: SHY/IEF/TLT proxies.
- Dealer gamma: explicitly a SPY-option gamma/OI proxy, not dealer inventory.
- Stream order flow: quote/trade inference from the available Tradier stream, not a full depth-of-book feed.

Source, representation, freshness and requiredness are persisted with the context state. Missing required data lowers health or blocks entry rather than being imputed as healthy.

## Forecast horizons

| Horizon | Role | Default cadence | Trade eligible |
|---|---|---:|---|
| 5m | timing | 1 minute | no |
| 15m | primary trade horizon | 1 minute | yes |
| 30m | primary confirmation | 1 minute | no |
| 60m | advisory | 5 minutes | no |
| 120m | advisory | 5 minutes | no |
| EOD | advisory | 5 minutes | no |
| 1D | research | 15 minutes | no |
| 5D | research | 15 minutes | no |

Slow horizons are intentionally calculated less frequently to control compute without making the 15-minute decision stale. Exchange-session targets are resolved with the market calendar; daily horizons advance by trading sessions rather than naive wall-clock days.

## Hierarchical regimes

Each decision carries simultaneous regime state at four scales:

- **Micro**: roughly 45 one-minute observations.
- **Intraday**: roughly 240 observations.
- **Swing**: roughly 780 observations.
- **Structural**: roughly 1,950 observations.

The classifier reports volatility state, correlation state/trend, breadth, concentration, gamma proxy, session, event state, transition risk, risk tone, volatility term structure and liquidity state. Conflicting scales raise transition uncertainty rather than being collapsed into a single chart-timeframe label.

## Physical distribution (P)

The P model uses point-in-time constituent returns and weights with a dynamic covariance estimator. A Student-t constituent simulation produces a SPY terminal distribution and explicit tail uncertainty. The deterministic alpha anchor is conditioned on the feature/context/regime state and is walk-forward calibrated using only outcomes that were already mature at prediction time.

If constituent coverage is insufficient, the runtime may use an explicit SPY fallback distribution, but the degraded source/coverage is recorded and can block strategy eligibility through surface/input gates.

## Risk-neutral distribution (Q)

The Q model uses SPY and fresh constituent IV observations, pragmatic smile slices, realized dependence and an independently modeled correlation-risk premium. It creates a synthetic SPY risk-neutral distribution for comparison with P and with executable SPY option quotes.

Insufficient constituent option coverage falls back to an explicit SPY-IV Q distribution. It is never represented as full constituent-surface coverage.

## Path outputs

Every primary forecast includes more than terminal direction:

- continuation and reversal probability;
- upside/downside one-sigma touch probability;
- first-touch ordering;
- squeeze and liquidation probability proxies;
- path archetype;
- MFE and MAE quantiles;
- terminal quantiles;
- expected IV change and model uncertainty.

## Strategy valuation

All enabled structures must have computable worst-case loss before entry. Candidate valuation:

1. uses executable opening bid/ask prices;
2. simulates the physical horizon distribution;
3. reprices option legs at the forecast horizon with **remaining 0DTE tenor and IV**, rather than treating T+15 as expiration;
4. compares structure value under P and Q;
5. applies estimated closing spread/slippage, commissions and fees;
6. applies model/surface/regime uncertainty;
7. requires positive expected value after a doubled modeled friction stress;
8. applies family-specific path and regime fit.

`NO_TRADE` is a first-class outcome and is preferred whenever data, broker state, uncertainty or economics fail a hard gate.

## Entry, sizing and execution

- Entries are considered on a five-minute grid inside the configured entry window.
- The 15-minute forecast is the authoritative trade horizon; 5m and 30m forecasts provide timing/alignment evidence.
- One managed position and one configured contract are the default fail-closed scope.
- Maximum modeled loss, daily loss, trust, input health, P/Q coverage, uncertainty, account validity and broker reconciliation are hard gates.
- Production market data and sandbox execution use separate credentials/clients.
- A paper order can be submitted only when production market-data state is valid.
- Partial fills are adopted and reconciled rather than ignored.

## Position management

Positions are evaluated every minute using executable closing-side marks and strategy-aware rules. Controls include:

- debit/credit-specific loss stops;
- capped profit targets;
- MFE-based trailing protection;
- directional thesis invalidation;
- IV/mispricing edge invalidation;
- short-strike/boundary threat exits;
- payoff-tent invalidation;
- time stops;
- forecast-horizon exit;
- late-session risk reduction;
- operator flatten;
- hard forced-flat time.

Held option symbols are directly quoted when the strategy chain no longer contains them, so a missing candidate-chain mark cannot bypass a forced flatten. Broker-confirmed quantities and actual fills/fees are authoritative for settlement.

## Event handling

The runtime consumes a versioned local event-calendar artifact. It may be refreshed from a configured HTTPS source and is archived for replay/governance. Missing, malformed, stale or out-of-coverage event input becomes `unknown`; when event input is required, that blocks new entries. Macro and rebalance windows can be hard-blocked independently.

## Captured-tape replay

Tradier is not treated as a deep historical tick/options archive. The authoritative proof dataset is the forward-captured production tape in Alpha-SPY's journal/raw records.

Replay re-computes a deterministic sample of matured forecasts **as of the original timestamp** from stored state. It checks feature/config hashes, target metadata, regime, P/Q distribution summaries and forecast values. Missing evidence is a replay failure, not a skipped sample.

## Promotion evidence

Nightly validation evaluates the current model version against configured gates covering:

- minimum paper sessions and matured forecasts;
- verified-data fraction;
- 15m/30m directional and probability calibration;
- interval coverage;
- net paper P&L and profit factor;
- maximum drawdown;
- doubled-cost-stress P&L;
- sandbox fill rate and slippage;
- zero unresolved broker-reconciliation failures;
- realized loss versus modeled max-loss integrity;
- regime sample count, regime coverage and regime expectancy;
- deterministic replay.

The only successful status is `ELIGIBLE_FOR_MANUAL_LIVE_REVIEW`. The evidence JSON contains `automatic_live_enable: false`. Production approval is cryptographically bound to the model/config/validation fingerprint, so changing model logic or thresholds invalidates prior approval.

## What paper validation cannot prove

A sandbox cannot reproduce real queue position, market impact, production order-routing behavior or all live fill dynamics. Passing every gate is evidence that the system is technically and statistically ready for a manual live-risk review; it is not proof of future profitability and it is not permission to trade real money.
## Learned signal policy

Hand-set linear coefficients are a **cold-start collection fallback only**. They allow the system to begin building timestamp-valid paper evidence, but forecasts produced by that fallback are not sufficient for promotion. Once the configured minimum matured formal-anchor sample count exists, 5m/15m/30m signals are generated by a standardized walk-forward Ridge fit trained only on outcomes confirmed at or before the forecast timestamp. The 15m/30m promotion sample must be at least 90% trained-model observations. Training uses non-overlapping formal anchors, current decision fingerprint only, and no future or revised inputs.

The learned feature vector includes constituent pressure/breadth/dispersion, realized and correlation state, cross-asset proxies, internal TICK/TRIN proxies, session auction/VWAP location, observable stream microstructure and option-chain activity. Direct futures, exchange depth/cancellation data, official NYSE TICK/TRIN and complex-order/sweep classification remain explicitly unavailable unless a future authoritative feed is connected; the runtime never relabels proxies as direct observations.

# Alpha-SPY v3.0 Data Specification

## Universal timestamp rule

All model joins are backward as-of joins. Timestamps are normalized to UTC while exchange-local session/calendar metadata is retained. No production prediction may consume a value whose effective timestamp is later than the prediction timestamp.

Every source state should expose, where applicable: `source`, `representation`, `effective_at`, `observed_at`, `age`, `stale`, `required`, `coverage`, and `quality`.

## Point-in-time constituent universe

| Field | Requirement |
|---|---|
| effective_at | required UTC timestamp |
| ticker | required historical ticker |
| weight | required point-in-time index weight |
| sector | required or explicitly unknown |
| permanent_id | recommended stable identifier |
| shares/divisor contribution | optional validation fields |

The runtime reports constituent count and covered weight. Incomplete required coverage is a risk gate.

## Equity/ETF/index quote state

Required normalized fields when supplied by the source:

`timestamp, symbol, bid, ask, bid_size, ask_size, last, volume, exchange, condition`

Derived state may include:

`mid, spread_bps, return, quote_age, dollar_volume, coverage, source_quality`

Core real-time symbols include SPY, QQQ and IWM plus point-in-time constituents. Context may include sector ETFs, HYG, UUP, SHY, IEF, TLT and VIX-family symbols when available.

## Stream microstructure state

The available production stream is summarized between synchronized one-minute snapshots:

- trade count and trade volume;
- inferred buy/sell/neutral volume using trade-versus-quote state;
- quote update count;
- average spread bps;
- large-trade ratio and maximum trade size.

These are **trade/quote proxies**, not full depth-of-book, cancellation or exchange queue data. The representation is persisted explicitly.

From the captured SPY constituent tape the runtime additionally derives explicitly labelled internal breadth proxies: one-minute up/down TICK-like breadth, session advance/decline state and a TRIN-like advance/decline-volume ratio. These are not official NYSE TICK/TRIN feeds. SPY cumulative volume snapshots are also converted into a replayable session VWAP/value-area/opening-range auction-profile proxy.

## Option quote state

Normalized contract fields:

`timestamp, underlying, symbol, expiration, strike, right, bid, ask, bid_size, ask_size, volume, open_interest, iv, delta, gamma, theta, vega`

Reference requirements include multiplier, expiration and right/strike. Raw provider payload is retained where practical. Crossed/impossible/stale observations are excluded or penalized by health/strategy gates.

## Constituent IV state

Rotating observations store point-in-time constituent IV/skew context with timestamp and underlying weight. P/Q modeling uses only observations available as of the forecast timestamp and reports the covered constituent weight.

## Context representation

Until dedicated institutional feeds are installed, the runtime uses explicit proxies:

| Desired state | Runtime representation |
|---|---|
| ES | SPY cash proxy |
| NQ | QQQ cash proxy |
| RTY | IWM cash proxy |
| credit | HYG ETF proxy |
| dollar | UUP ETF proxy |
| front/intermediate/long rates | SHY/IEF/TLT ETF proxies |
| dealer gamma | SPY option OI × gamma proxy |
| TICK/TRIN | SPY-constituent internal breadth/volume proxies |
| auction/profile | captured SPY session VWAP/value-area/opening-range proxy |
| option flow | chain volume/OI/delta-activity proxy; aggressor side not claimed |
| futures/order-book state | unavailable unless a dedicated source is added |
| depth/cancellations/sweeps/complex-order classification | unavailable unless a dedicated source is added |

Proxy use decreases information specificity; it must never be labeled as a direct feed.

## Events

The event adapter consumes a versioned JSON object containing at minimum:

`version, generated_at, valid_from, valid_through, source, events[]`

Each event includes timestamp/window and a normalized type such as:

`macro_announcement, earnings_heavy, rebalance, ordinary, unknown`

Missing, stale, malformed or out-of-coverage event data becomes `unknown` and blocks entry when event input is configured as required. Accepted calendar artifacts are archived for replay.

## Market calendar

Exchange-session targets use the production market calendar when available. The resolved forecast target and calendar metadata are frozen into each prediction so replay does not reinterpret the target under a later calendar response.

## Features

Feature records are deterministic functions of point-in-time observations and carry a canonical feature hash. Principal features include weighted return, breadth, dispersion, concentration, residual/pressure state, rolling correlation/downside correlation, trust/health, IV/skew context and source quality.

## Regime state

Persisted prediction state carries micro/intraday/swing/structural regime context including volatility, correlation, breadth, concentration, gamma proxy, session, event, transition/conflict risk, risk tone, volatility term state and liquidity.

## Forecast record

Every horizon prediction freezes:

- snapshot/feature/config hashes;
- model version and horizon role;
- created and target timestamps;
- full frozen P/Q probability grid and price quantiles plus summary/source;
- probability up/down and interval;
- path archetype and touch/first-touch probabilities;
- continuation/reversal/squeeze/liquidation probabilities;
- MFE/MAE and terminal quantiles;
- expected IV change;
- context/regime/event state;
- input/surface coverage and model uncertainty;
- multi-horizon consensus where applicable;
- champion signal provenance/training sample metadata and formal-anchor shadow challenger inference when available.

## Strategy/candidate record

Candidate evidence includes legs, executable entry economics, max loss/profit, P expected value, Q value/edge, expected liquidation cost, total round-trip friction, doubled-cost expected value, model uncertainty, edge-to-uncertainty ratio, strategy/regime/path fit and eligibility reasons.

## Execution and position records

Paper broker records include broker order ID, status, requested/filled/remaining quantity, average fill, preview/fee information where returned, repricing/cancel events and reconciliation state. Position settlement uses broker-authoritative quantities and actual fill/fee data when broker submission is enabled.

## Replay and validation evidence

Replay records freeze sample IDs, method, checked/mismatched/error counts and mismatch detail. Validation records freeze gate thresholds, metrics, failed gates, current model version/config fingerprint and replay result. Promotion evidence is immutable-by-content and is referenced by SHA in any later production approval artifact.

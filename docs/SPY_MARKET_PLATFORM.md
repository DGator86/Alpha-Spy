# SPY Market Platform — Processor Architecture

## Purpose

The platform is a market-intelligence system, not an execution system.

Alpha, Beta, and Gamma remain logically independent models. Delta compiles their outputs into a standardized market state and read-only analytical streams. Bots consume those streams and publish assertions. Managers synthesize assertions. Broker execution is outside this package and outside the Alpha/Beta/Gamma/Delta authority chain.

## Frozen migration baselines

- Alpha-SPY main: `4cc4907a87792a4f3f2a8c07d965b9447d2e8b16`
- Beta-spy main: `6fc415edc99bb04084a199d336ea5711b568fa35`

Migration work must preserve the independent outputs of those baselines unless a separately documented model change is under test. Infrastructure can converge; hypotheses must not be blended before Delta.

## Canonical architecture

```text
                    TRADIER + OTHER RAW DATA
                              |
                              v
                    SPY MARKET PLATFORM
             ingestion / clock / validation /
                persistence / replay / quality
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
           ALPHA             BETA            GAMMA
        statistical       internals/tape    derivatives
             |                |                |
             +----------------+----------------+
                              |
                              v
                            DELTA
                   market-state compiler
                              |
         +--------------------+--------------------+
         |                    |                    |
         v                    v                    v
     data streams        conflict stream       anomalies
         |                    |                    |
         +--------------------+--------------------+
                              |
                              v
                         QUANT BOT DESK
                              |
                         assertions only
                              |
                              v
                         QUANT MANAGER
```

A separate external-information desk handles macro, rates/Fed, news/catalysts, and sentiment/information flow. Those assertions route to the Economist. Quant Manager and Economist are consumers of assertions, not replacements for the underlying models.

## Model authority

### Alpha

Answers: *What does the mathematical state of the market imply?*

Publishes statistical distributions, covariance/correlation/dispersion, hierarchical regime state, lifecycle/survival, multi-horizon forecasts, path/MFE/MAE/touch/reversal measures, and calibrated uncertainty.

Alpha does not consume news narrative and does not choose or execute a trade in the processor architecture.

### Beta

Answers: *What are the securities underneath SPY actually doing?*

Publishes equal- and SPY-weighted breadth, sectors, leadership/laggards, VWAP state, momentum synchronization, participation, flow and top-of-book proxies, auction/microstructure state, and independent causal forecasts.

Beta remains an independent hypothesis. Its forecast must not be folded into Alpha before Delta.

### Gamma

Answers: *What does the derivatives market imply and what positioning can alter SPY's path?*

Gamma v0.1 extracts the option-chain substrate already collected by Alpha and publishes a separate derivatives state. The first implementation includes multi-expiration IV/skew/term structure, OI/volume concentration, unsigned activity, liquidity, pin proxies, and a transparent call-positive/put-negative OI gamma proxy.

The proxy is explicitly **not** a dealer inventory feed. Unsigned chain volume is explicitly **not** aggressor-side options flow. Vanna/charm remain unavailable until they are implemented from defensible inputs.

### Delta

Delta is a deterministic compiler, not a fourth predictor.

It preserves all three model outputs and mechanically computes convergence, divergence, sign conflicts, model-version provenance, source freshness, data quality, and state changes. Delta has no trade, sizing, instrument, strategy, order, or broker authority.

## Delta streams

The initial read-only API exposes:

- `direction`
- `regime`
- `path`
- `breadth`
- `flow`
- `volatility`
- `options_positioning`
- `liquidity`
- `divergence`
- `anomalies`
- `data_quality`

Bots receive bounded views instead of one unrestricted master blob.

## Quant desk

Five analytical roles are defined:

1. Direction & Momentum
2. Market Internals
3. Volatility & Derivatives
4. Statistical & Regime
5. Quant Skeptic / Red Team

They publish timestamped assertions only. The Quant Manager synthesizes those assertions.

## External-information desk

Four initial analytical roles are defined:

1. Macro
2. Rates / Fed
3. News / Catalyst
4. Information Flow / Sentiment

They publish assertions to the Economist. This desk stays separate from Alpha/Beta/Gamma so narrative cannot silently contaminate quantitative measurements.

## Audit rule

Every model state, assertion, and manager view should be frozen before its outcome is known. Later research can score conditional competence by regime, horizon, session, volatility state, catalyst state, and other measurable conditions.

## Execution boundary

Existing Alpha execution code remains untouched on this research branch because it is part of the frozen legacy baseline. The new `spy_platform` package does not import the execution, risk, strategy, order, or broker modules. Its runtime bridge reads Alpha persistence and Beta's state API only.

If an execution bot is added later, it must live outside this processor package and consume a separately approved decision contract.

## Phase sequence

1. Establish shared contracts and read-only Delta service without changing Alpha/Beta behavior.
2. Move shared raw-data ingestion, clock, persistence, replay and provenance into platform infrastructure.
3. Migrate Alpha and Beta behind those contracts with replay-equivalence tests against the frozen baseline SHAs.
4. Expand Gamma to 0DTE, 1DTE and weekly derivatives state with defensible term-structure and positioning measures.
5. Harden Delta convergence/divergence and source-quality telemetry.
6. Connect quant bots in shadow/assertion-only mode.
7. Connect external-information analysts and Economist in shadow mode.
8. Add immutable assertion/outcome scoring and conditional role scorecards.
9. Only then design a separate decision/risk/execution organization.

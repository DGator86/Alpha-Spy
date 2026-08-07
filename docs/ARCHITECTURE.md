# Architecture

## Operating principles

1. Prediction, execution and confirmation are separate tapes.
2. Every prediction is immutable after creation.
3. The confirmation service cannot initiate or alter a trade.
4. Intraday controls may only reduce risk, pause entries or request a managed flatten.
5. Model promotion occurs outside the trading session after explicit evidence gates.
6. The GUI queues authenticated commands; the execution engine remains authoritative.

## Runtime services

### Market collector

Runs every 60 seconds by default. It records:

- SPY, QQQ and IWM quotes
- Point-in-time quotes for the configured constituent universe
- Constituent weights and sectors
- SPY 0DTE or nearest option chain for strategy construction
- A separate 3–14 DTE SPY IV reference surface
- Rotating IV/skew observations for priority constituents
- Feed freshness, coverage and integrity metadata

### Engine

For each new snapshot:

1. Computes weighted constituent return and breadth.
2. Computes concentration, dispersion, residual pressure and rolling correlation.
3. Attaches SPY-versus-constituent IV and skew context.
4. Freezes a 15-minute probability distribution.
5. Generates all enabled defined-risk or debit-paid option structures.
6. Calculates conservative expectancy using executable bid/ask prices and fees.
7. Applies trust, health, loss, time and account limits.
8. Records a no-trade or order decision.
9. Publishes the state to the GUI.

### Confirmation service

At forecast maturity it:

- Locates the nearest point-in-time realized snapshot.
- Scores direction, terminal error, range coverage and Brier loss.
- Computes realized path high and low.
- Re-queries Tradier 1-minute time-and-sales where available to detect data revisions.
- Scores every candidate structure against an executable closing mark.
- Marks formal non-overlapping 15-minute anchors for governance statistics.

### Settlement service

Marks the managed position using executable closing-side prices, tracks MFE/MAE and applies:

- Profit target
- Risk stop
- Operator flatten
- Forced-flat time

Live exits must preview and reach a broker-confirmed fill before the local position is marked closed.

### Dojo

Produces post-session governance reports. It does not modify the live model intraday.

## Persistence

SQLite runs in WAL mode. High-volume snapshots are also written to dated JSONL files. The v2 runtime database is isolated from prior installations.

## Deployment boundary

All APIs bind to `127.0.0.1`. The expected access path is an SSH tunnel. This keeps the dashboard, tokens and control endpoints off the public internet by default.

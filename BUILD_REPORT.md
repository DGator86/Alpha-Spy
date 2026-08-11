# Alpha-SPY 3.0.0 Build & Readiness Report

Generated: 2026-08-10
Release posture: **real-time production market data + Tradier sandbox paper execution only**
Production-money posture: **locked; manual review and separate evidence-bound authorization required**

## Release objective

Alpha-SPY 3.0.0 is the single consolidated paper-validation release. It is designed to get the system as close as practical to a production trading environment without risking real capital. The decision engine consumes real-time production market observations, sends broker orders only to the sandbox virtual account during proof, records broker lifecycle/fills, and accumulates immutable evidence for a later manual live review.

## Implemented architecture

- One authoritative Python namespace: `alpha_spy`.
- Tradier production REST/WebSocket market-data credential isolated from the sandbox execution credential.
- Synchronized one-minute captured tape built from continuous production streaming.
- SPY + point-in-time constituent universe plus QQQ/IWM, sectors, credit, dollar, Treasury-ETF rate proxies and VIX-family context.
- Explicit ES/NQ/RTY cash proxies; direct futures are never fabricated.
- Stream trade/quote microstructure: aggressor proxy, spreads, large-trade activity and quote/trade counts.
- Internal SPY-constituent TICK/TRIN-like breadth proxies and replayable SPY session VWAP/value-area/opening-range profile proxy.
- SPY option-chain activity proxy, IV/skew surface, OI/gamma dealer-gamma proxy and constituent IV context.
- Versioned external event-calendar adapter with stale/missing/out-of-window fail-closed behavior.
- Hierarchical micro/intraday/swing/structural regime model.
- Full forecasting stack: 5m, 15m, 30m, 60m, 120m, EOD, 1D and 5D.
- Walk-forward standardized Ridge champion signal. Fixed coefficients are cold-start only and are not sufficient for promotion.
- 15m/30m formal-anchor nonlinear HistGradientBoosting shadow challenger with zero trading/risk/promotion authority.
- Constituent Student-t physical P distribution with dynamic covariance.
- Synthetic risk-neutral Q distribution, IV smile/correlation-risk-premium context and full frozen P/Q quantile grids.
- Path outputs: continuation/reversal, first-touch ordering, squeeze/liquidation, MFE/MAE and terminal quantiles.
- Horizon-correct 0DTE option repricing preserving remaining tenor and forecast IV change.
- P/Q/cost/uncertainty-adjusted strategy ranking with `NO_TRADE` as a first-class outcome.
- 5-minute entry grid; one-minute managed-position monitoring.
- Strategy-aware stops, profit targets, trailing exits, thesis/IV invalidation, time/horizon exits, late-session reduction and 15:55 ET forced flat.
- Broker account validity, buying-power and reconciliation hard vetoes.
- Partial-fill adoption, cancel/replace handling, direct held-leg mark fallback, broker-authoritative quantities and actual-fill/fee settlement.
- Deterministic captured-tape as-of replay including full P/Q distribution-shape verification.
- Nightly objective promotion evaluator and dashboard visibility for failed gates.
- Existing confirmation tape, Dojo, research tree, dashboard, backup and systemd service architecture retained.

## Paper-validation policy

The shipped policy requires all gates to pass. Key minimums include:

- 60 paper sessions.
- 5,000 matured forecasts.
- 750 non-overlapping formal-anchor observations at both 15m and 30m.
- 60 closed sandbox trades.
- >=99% verified production-stream/input coverage.
- >=90% P/Q-ready forecasts.
- >=90% trained Ridge signal fraction on 15m and 30m formal evidence.
- 15m direction >=53% and 95% Wilson lower bound >=50%.
- 30m direction >=52.5% and 95% Wilson lower bound >=50%.
- Brier ceilings and Brier-skill gates.
- 72%-88% primary interval coverage.
- Net P&L >=0, profit factor >=1.15 and one-sided 95% expectancy lower bound >=0.
- Last 20 trades net >=0 and profit factor >=1.0.
- Max drawdown <=$600.
- Doubled-modeled-friction P&L >=0.
- Sandbox broker fill fraction >=95%; mean fill slippage <=$12.
- Zero unresolved reconciliation errors.
- No realized loss >1.10x modeled max loss.
- At least three tested regime buckets covering >=75% of trades, with nonnegative expectancy in sufficiently sampled buckets.
- Deterministic replay of the current decision fingerprint with zero mismatches.

Passing yields only `ELIGIBLE_FOR_MANUAL_LIVE_REVIEW`. It never automatically enables real-money trading.

## Verification performed

### Source/runtime tests

`python -m pytest -o addopts='' -q`

Result: **243 passed**.

### Static/package checks

- `python -m compileall -q src tests examples` — PASS.
- `bash -n install.sh scripts/*.sh scripts/alpha-spy-backup` — PASS.
- `node --check src/alpha_spy/dashboard/static/app.js` — PASS.
- `scripts/verify_units.sh` — PASS; **15 systemd units verified**.
- `scripts/check_legacy_identifiers.sh` — PASS.
- `git diff --check` — PASS.
- Ruff: the execution environment's package index does not expose a Ruff distribution, so Ruff could not be reproduced locally. The repository retains the pinned Ruff configuration and GitHub CI is expected to run it.

### Wheel build

Built successfully with no dependency resolution:

`python -m pip wheel . --no-deps --no-build-isolation`

Artifact: `alpha_spy-3.0.0-py3-none-any.whl`
SHA-256: `4399b6fbfea929b9705638a69e3b7cdfed283d8d74455aca5a3f2a0811c64838`

### Installed-wheel smoke

The built wheel was installed into a clean target directory with `--no-deps` and exercised using the runner's installed runtime dependencies. The smoke created a fresh journal, loaded the local universe, created a deterministic demo snapshot, ran the hardened engine, emitted a fail-closed `NO_TRADE` decision and built dashboard state.

Result: **INSTALLED_WHEEL_SMOKE_OK**.

A fully isolated venv bootstrap was not reproducible in this runner because its package index could not supply all bootstrap tooling; this is an infrastructure limitation rather than an Alpha-SPY source failure. GitHub CI remains the authoritative isolated dependency/build check after upload.

## Data-source boundaries

Alpha-SPY explicitly does **not** claim data that the configured Tradier interface does not provide. Direct ES/NQ/RTY futures, exchange L2 depth/queue/cancellation messages, official NYSE TICK/TRIN, direct Treasury yields, and institutional sweep/complex-order classification remain unavailable unless a dedicated authoritative feed is added. Their currently available information classes are represented by clearly labelled proxies where possible and by missing/uncertainty state otherwise.

This matters for later live review: sandbox execution and real-time market observation can validate model logic, timing, broker lifecycle, risk controls and paper expectancy, but cannot fully reproduce real-money queue priority, market impact or adverse selection. The promotion gates reduce this uncertainty; they do not make it disappear.

## Secret handling

No production or sandbox API key/account value is stored in the repository. `config/secrets.env.example` contains only empty placeholders. Runtime secrets belong in `/etc/alpha-spy/secrets.env` with restricted permissions.

## Final readiness statement

Alpha-SPY 3.0.0 is ready for **real-time paper-validation deployment**, not automatic real-money deployment. The next work after installation is evidence collection, not architectural feature development. Do not alter the champion decision/risk configuration during a proof window unless intentionally restarting the decision fingerprint and validation clock.

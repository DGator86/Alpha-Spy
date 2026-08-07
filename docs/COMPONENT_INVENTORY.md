# Complete Component Inventory

## Runtime services

| Service | Installed unit | Responsibility |
|---|---|---|
| Market collector | `spy-der-market.service` | Constituent, benchmark, SPY, option-chain, IV and skew collection; immutable market tape |
| Forecast and decision engine | `spy-der-engine.service` | Features, 15-minute distributions, strategy candidates, risk gates, decisions and execution handoff |
| Confirmation engine | `spy-der-confirmation.service` | First post-target realization, historical revision checks, forecast scores and candidate counterfactuals |
| Settlement monitor | `spy-der-settlement.service` | Executable closing marks, MFE/MAE, stops, targets, forced flat and managed closing orders |
| Decision API | `spy-der-decision.service` | Local health/state API and authenticated supervisory controls |
| Command Center GUI | `spy-der-dashboard.service` | Browser dashboard, live WebSocket state, confirmation tape, trade monitor, audit lab and operations |
| Dojo governance | `spy-der-dojo.service` | After-hours calibration, strategy and champion/challenger evidence report |
| Google Drive backup | `spy-der-backup.service` | Online SQLite snapshots, compression, raw-data copy and manifests |

## Timers

- `spy-der-dojo.timer`: weekdays at 6:30 PM Eastern.
- `spy-der-backup.timer`: every day at 5:00 PM Eastern.

## Trading and research modules

- Point-in-time constituent universe and weight handling.
- Tradier REST client, normalization, batching, retry and rate-state capture.
- Weighted breadth, constituent pressure, residual pressure, concentration, dispersion and dependence features.
- Immutable T+15 forecasts with model, configuration and feature hashes.
- Long calls and puts, debit spreads, defined-risk credit spreads, straddles, strangles, iron condors and butterflies.
- Executable bid/ask candidate pricing, fee estimates, liquidity penalties and bounded-risk screening.
- Paper execution and fail-closed live preview, submit, reprice, cancel and terminal-state confirmation.
- Position monitoring using executable closing-side quotes.
- Formal non-overlapping confirmation anchors and full-resolution diagnostic forecasts.
- Data revision status, calibration metrics, counterfactual outcomes and after-hours promotion gates.
- Separate research package for simulation, covariance, lead/lag, stress, policy and strategy-tournament work.

## Persistent data

The installer preserves existing data and adds isolated v2 databases:

- `/var/lib/spy-der/journal/suite-v2.db`
- `/var/lib/spy-der/dashboard/command-center-v2.sqlite`
- `/var/lib/spy-der/market/`
- `/var/lib/spy-der/candidates/`
- `/var/lib/spy-der/audit/`
- `/var/lib/spy-der/positions/`
- `/var/lib/spy-der/reports/dojo/`
- `/var/lib/spy-der/models/`
- `/var/lib/spy-der/reference/`

The legacy roots `/var/lib/spy-der` and `/var/lib/zerodte` are included in the Google Drive backup without deleting source or destination data.

## Installation and operating tools

- `install.sh`: complete installer entrypoint.
- `scripts/preflight.sh`: capacity, process, database and Drive checks.
- `scripts/configure_tradier.sh`: protected credential configuration.
- `scripts/status.sh`: consolidated runtime status.
- `scripts/doctor.sh`: database, configuration, API and timer validation.
- `scripts/backup_now.sh`: immediate backup run.
- `scripts/reset_dashboard_tokens.sh`: token rotation.
- `scripts/production_unlock.sh`: explicit guarded live-submission unlock.
- `scripts/production_lock.sh`: immediate return to paper/locked mode.
- `scripts/export_support_bundle.sh`: redacted diagnostics archive.
- `scripts/uninstall.sh`: removes software while preserving data and configuration.

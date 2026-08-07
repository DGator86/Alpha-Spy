# Complete Component Inventory

## Runtime services

| Service | Installed unit | Responsibility |
|---|---|---|
| Market collector | `alpha-spy-market.service` | Constituent, benchmark, SPY, option-chain, IV and skew collection; immutable market tape |
| Forecast and decision engine | `alpha-spy-engine.service` | Features, 15-minute distributions, strategy candidates, risk gates, decisions and execution handoff |
| Confirmation engine | `alpha-spy-confirmation.service` | First post-target realization, historical revision checks, forecast scores and candidate counterfactuals |
| Settlement monitor | `alpha-spy-settlement.service` | Executable closing marks, MFE/MAE, stops, targets, forced flat and managed closing orders |
| Decision API | `alpha-spy-decision.service` | Local health/state API and authenticated supervisory controls |
| Command Center GUI | `alpha-spy-dashboard.service` | Browser dashboard, live WebSocket state, confirmation tape, trade monitor, audit lab and operations |
| Dojo governance | `alpha-spy-dojo.service` | After-hours calibration, strategy and champion/challenger evidence report |
| Google Drive backup | `alpha-spy-backup.service` | Online SQLite snapshots, compression, raw-data copy and manifests |

## Timers

- `alpha-spy-dojo.timer`: weekdays at 6:30 PM Eastern.
- `alpha-spy-backup.timer`: every day at 5:00 PM Eastern.

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

The installer creates the Alpha-SPY data roots:

- `/var/lib/alpha-spy/journal/alpha-spy.db`
- `/var/lib/alpha-spy/dashboard/command-center.sqlite`
- `/var/lib/alpha-spy/market/`
- `/var/lib/alpha-spy/candidates/`
- `/var/lib/alpha-spy/audit/`
- `/var/lib/alpha-spy/positions/`
- `/var/lib/alpha-spy/reports/dojo/`
- `/var/lib/alpha-spy/models/`
- `/var/lib/alpha-spy/reference/`

The data root `/var/lib/alpha-spy` is included in the Google Drive backup without deleting source or destination data.

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

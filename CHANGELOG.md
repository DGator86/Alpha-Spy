# Changelog

## 3.0.0 — 2026-08-10

- Unified the production-real-time-data / Tradier-sandbox-paper operating mode.
- Added 5m/15m/30m/60m/120m/EOD/1D/5D forecast horizons with bounded compute cadences.
- Wired dynamic-covariance Student-t physical P and constituent-smile synthetic Q distributions into runtime.
- Added implied-vs-realized correlation risk premium, uncertainty scoring and doubled-cost candidate gates.
- Added path archetype, level-touch order, continuation/reversal, squeeze/liquidation, MFE/MAE and IV-change forecasts.
- Added hierarchical micro/intraday/swing/structural regime classification with cross-horizon transition risk.
- Expanded real-time context with HYG, UUP, Treasury ETF proxies, VIX-family indices, sectors and stream microstructure proxies.
- Added versioned event-calendar ingestion; stale/missing/out-of-coverage calendars fail closed.
- Added deterministic captured-tape replay and persisted replay manifests.
- Added objective paper-promotion evaluation across data, forecast calibration, P&L, doubled costs, drawdown, fills, reconciliation, modeled loss, regimes and replay consistency.
- Made paper validation incapable of automatically enabling live money; passed reports only become eligible for manual review.
- Bound any later production approval to a passed validation report, model version, configuration/validation fingerprint and evidence SHA-256.
- Added nightly validation systemd service/timer and an optional premarket event-calendar refresh timer.
- Dashboard state now surfaces the full horizon stack, P/Q diagnostics, hierarchical regime/context, replay and promotion gates.


## Unreleased

### Clean break: Alpha-SPY

Alpha-SPY is a standalone product. It is not an upgrade to, and shares no
identity or data with, any previous trading application. This release removes
all prior naming and every trace of migration logic.

Canonical naming throughout:

| | |
|---|---|
| Product | Alpha-SPY |
| Python package | `alpha_spy` |
| CLI | `alpha-spy` |
| Distribution | `alpha-spy` |
| Install root | `/opt/alpha-spy` |
| Configuration | `/etc/alpha-spy` |
| State and data | `/var/lib/alpha-spy` |
| Logs | `/var/log/alpha-spy` |
| Service account | `alphaspy` |
| Units | `alpha-spy-*.service`, `alpha-spy-*.timer`, `alpha-spy.target` |
| Databases | `alpha-spy.db`, `command-center.sqlite` |
| Environment | `ALPHA_SPY_*` |

Removed rather than adapted:

- A previous product's data root, which the backup, preflight and support
  bundle used to reach into.
- The obsolete backup unit the installer used to stop and disable.
- The preservation of a previous installation tree and a timestamped copy of
  its configuration under `/var/backups`.
- The recovery of broker credentials and dashboard tokens from a previous
  configuration. Every install now issues fresh view, administrator and
  ingestion tokens, and Tradier is configured afterwards with
  `configure_tradier.sh`.
- Database filename suffixes that existed only so a previous system's files
  would not be overwritten.
- The previous product's release archives. This repository is not their
  continuation.

The installer assumes a fresh installation and touches only Alpha-SPY paths,
units and the `alphaspy` account. It does not inspect, stop, modify or depend
on any other application on the host. `docs/UPGRADE.md` is rewritten around
Alpha-SPY's own upgrade and rollback.

The Dojo is retained in full and moved into the namespace
(`alpha-spy-dojo.service`, `alpha-spy-dojo.timer`, `reports/dojo`,
`models/challengers`). Constituent intelligence, prediction, options
valuation, strategy selection, risk control, execution, the confirmation tape,
continuous audit, champion/challenger, the GUI Command Center and backup are
all unchanged. This is a namespace cleanup, not a feature reduction.

`scripts/check_legacy_identifiers.sh` fails the build if any prohibited
identifier reappears anywhere in the repository, and runs in `make lint` and CI
static validation. It carries no exemptions: this changelog deliberately
describes the change without naming the identifiers it removed.

### Correctness fixes

- **Account state could not be parsed for a flat account.** `parse_account_state`
  fell through an `or` chain to `get("day_trade_buying_power_used") * 0.0`,
  which raised `TypeError` whenever that key was absent — including the normal
  case of `open_pl == 0.0`. With Tradier configured, every engine cycle then
  logged "Account poll failed" and substituted a fabricated $25,000 equity, so
  risk was sized off a phantom balance.
- **A real zero balance was replaced by the $25,000 default.** The same `or`
  chains treated a legitimate `0.0` equity, cash or P&L as a missing key. A
  blown account was sized as if it held $25,000; it now yields zero allowed
  risk and blocks entries.
- **The forced flat could be silently disabled.** It compared
  `strftime("%H:%M")` against the raw configured string, and `"15:00" >= "9:55"`
  is false lexicographically, so any non-zero-padded time disabled the control
  entirely — a position would never be flattened. Times are now parsed, and
  `RiskConfig` normalises and validates them.
- **Risk limits were unvalidated.** `maximum_contracts`, loss limits, trust and
  multipliers now carry explicit bounds, so a typo fails at load instead of
  loading silently.
- **SQLite connections were never closed on read paths.** `with connect() as
  con` commits but does not close, leaving handles to the cyclic collector —
  124 file descriptors leaked per 6,000 reads. Under WAL those lingering
  readers hold locks that prevent checkpointing, so the `-wal` file grows all
  session. Both the journal and the dashboard repository now close.
- **`tabulate` was undeclared.** Every research driver calls
  `DataFrame.to_markdown()`, so all four ran their full simulation and then
  crashed writing the report. Added as a `research` extra (and to `dev`).
- **`to_numpy()` returned a read-only array under pandas 3.0.** The
  correlation-risk-premium write in `SyntheticRiskNeutralModel.simulate`
  raised; the suite permits `pandas<4`.
- **Operator scripts assumed system PyYAML.** `production_lock.sh`,
  `production_unlock.sh` and `export_support_bundle.sh` invoked bare `python3`
  to edit `/etc/alpha-spy/config.yaml`, but PyYAML ships with the wheel and the
  installer never added `python3-yaml`. They now prefer the suite interpreter,
  and the installer provides a fallback.
- **`doctor` reported `ok` while entries were blocked.** Its verdict considered
  only database integrity, so it answered `ok` with a 20-symbol universe
  against a configured minimum of 450 — the state in which the coverage gate
  refuses every entry. It now reports `degraded` with the reason, while still
  exiting non-zero only on a hard database failure so installs are unaffected.

### Tests

- Coverage grew from 27 to 178. New suites cover the documented risk controls
  and production lock, Eastern-time handling across both DST offsets,
  connection lifetime and concurrent writers, the research drivers end to end,
  and static guards over the installer, operator scripts and systemd units.
- `run_synthetic_demo` is now a golden test: it must reproduce the committed
  `examples/synthetic_edge_output.csv` byte for byte.

- Cleared all ruff findings across `src/`, `tests/` and `examples/`, and
  promoted the ruff CI job from advisory to gating.
- Pinned the ruff rule set explicitly in `pyproject.toml`. Previously only
  `line-length` was configured, so ruff applied its full current default
  (826 rules) and any upgrade could reintroduce findings.
- `OptionRight` is now a `StrEnum` rather than `(str, Enum)`. This is a
  behaviour change: `str(OptionRight.CALL)` returns `"C"` instead of
  `"OptionRight.CALL"`, which is what the codebase's
  `str(row.right).startswith("C")` idiom already assumed.
- Removed dead locals in the research drivers, including a `holdout` /
  `holdout_worlds` pair superseded by the `holdout_selected` path, and dropped
  a vestigial always-true report filter (`if line != "" or True`).
- Replaced `zip(bounds[:-1], bounds[1:])` with `itertools.pairwise`, and made
  the remaining `zip()` calls state `strict=` explicitly.

- Integrated the v2.0.0 suite source tree into the repository; the shipped
  release archives are preserved verbatim under `release/v2.0.0/`.
- Added `scripts/build_release.sh`: reproducible tar.gz and zip archives with a
  `RELEASE_MANIFEST.sha256` covering every file.
- Added `scripts/verify_release.sh`: checksum, manifest and installer-prerequisite
  verification for a release archive, runnable on the VPS before installing.
- Added `scripts/smoke_test.sh`: hermetic end-to-end exercise of the built wheel
  covering configuration, database integrity, a demo market and decision cycle,
  both API servers, and dashboard view/admin token separation.
- Added `scripts/deploy_vps.sh` and `make deploy` for SSH deployment.
- Added CI, release, deploy and GUI-preview GitHub Actions workflows.
- Extended the Makefile with `venv`, `smoke`, `release`, `verify-release`,
  `deploy` and `help` targets.
- Added [docs/BUILD_AND_DEPLOY.md](docs/BUILD_AND_DEPLOY.md).

## 2.0.0 — 2026-08-06

- Unified research engine, live runtime, audit tape and Command Center GUI.
- Added one-command Ubuntu VPS installer.
- Added separate v2 databases to preserve prior system data.
- Added point-in-time constituent quote collection.
- Added rotating constituent IV/skew collection and SPY reference surface.
- Added immutable T+15 predictions and formal non-overlapping anchors.
- Added defined-risk strategy generator and deterministic maximum-loss checks.
- Added paper execution and guarded Tradier preview/reprice/cancel workflow.
- Added managed position monitor and broker-confirmed live exits.
- Added historical-data revision checks and counterfactual candidate scoring.
- Added daily 5:00 PM Eastern Google Drive backup.
- Added hardened systemd deployment and separate GUI tokens.

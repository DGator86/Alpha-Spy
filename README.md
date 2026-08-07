# SPY Constituent Alpha Suite v2.0.0

A complete, single-VPS software suite for constituent-driven SPY options research, live market collection, immutable T+15 forecasting, defined-risk strategy selection, paper/live execution controls, confirmation auditing, model governance, Google Drive backup, and a secure browser GUI.

The default installation is deliberately fail-closed:

- Tradier environment: `sandbox`
- Order submission: disabled
- Paper mode: enabled
- Maximum contracts: one
- Maximum modeled loss per trade: $100
- Maximum trades per day: one
- Dashboard and decision APIs: bound to `127.0.0.1`
- Production requires both configuration changes and a separate sentinel file

## Included applications

| Component | Function |
|---|---|
| Market collector | SPY, benchmark and point-in-time constituent quotes; SPY option chain; rotating constituent option-IV collection |
| Forecast engine | Weighted breadth, pressure, residual pressure, concentration, dispersion, correlation and 15-minute distribution forecasts |
| Strategy engine | Long calls/puts, debit spreads, defined-risk credit spreads, straddles, strangles, iron condors and butterflies |
| Risk controller | Data-health gates, trust score, daily-loss limit, one-position rule, time windows and strict maximum-loss controls |
| Execution manager | Paper fills or guarded Tradier preview/limit/reprice/cancel workflow |
| Settlement monitor | Position marks, MFE/MAE, profit protection, risk stops, operator flatten and forced-flat handling |
| Confirmation tape | Immutable T=0 forecasts, T+15 realization, point-in-time revision checks, Brier score, interval coverage and counterfactual candidate outcomes |
| Dojo | Formal-anchor performance summaries and champion/challenger governance reports |
| Command Center GUI | Six-screen responsive operations, trading, audit and market-internals dashboard |
| Decision API | Local health, state and operator-control API |
| Google Drive backup | Daily 5:00 PM Eastern online SQLite snapshots and incremental raw-data backup |
| Research framework | Full constituent-alpha simulation, backtesting, stress testing, strategy tournaments and professional exit research |

## Build from source

```bash
make venv          # create .venv with runtime and dev dependencies
make verify        # lint, tests, wheel build, installed-wheel smoke test
make release       # dist/release/spy-constituent-alpha-suite-v2.0.0.{tar.gz,zip} + checksums
```

`make release` produces reproducible archives with a `RELEASE_MANIFEST.sha256`
covering every file. `make verify-release` re-checks them. Full details,
including the CI, release and deploy workflows, are in
[Build and deploy](docs/BUILD_AND_DEPLOY.md).

## Install on Ubuntu 24.04

Transfer the release archive to the VPS, verify it, extract it, and run one
installer:

```bash
cd /root
sha256sum -c spy-constituent-alpha-suite-v2.0.0.tar.gz.sha256
tar -xzf spy-constituent-alpha-suite-v2.0.0.tar.gz
cd spy-constituent-alpha-suite-v2.0.0
bash scripts/verify_release.sh ../spy-constituent-alpha-suite-v2.0.0.tar.gz
sudo bash install.sh
```

To install from a workstation over SSH instead:

```bash
DEPLOY_HOST=YOUR_VPS_IP make deploy
```

The installer:

1. Stops the prior SPY processes without deleting `/var/lib/spy-der` or `/var/lib/zerodte`.
2. Preserves the prior `/opt/spy-der` installation and configuration.
3. Installs the bundled Python wheel in `/opt/spy-der/venv`.
4. Creates a restricted `spyder` service user.
5. Installs all systemd services and timers.
6. Generates separate view, administrator and ingestion tokens.
7. Starts the complete suite in sandbox/paper mode.
8. Enables the Google Drive backup timer when the root `gdrive:` rclone remote is already authorized.
9. Writes connection details to `/root/spy-der-credentials.txt`.

## Open the GUI

From Windows PowerShell:

```powershell
ssh -L 8788:127.0.0.1:8788 -L 8787:127.0.0.1:8787 root@YOUR_VPS_IP
```

Open:

```text
http://127.0.0.1:8788
```

Read the generated view token:

```bash
sudo cat /root/spy-der-credentials.txt
```

## Configure Tradier

```bash
sudo /opt/spy-der/release/scripts/configure_tradier.sh
```

The suite remains in paper mode. Use the sandbox for verification before considering production.

## Verify the installation

```bash
sudo /opt/spy-der/release/scripts/status.sh
sudo /opt/spy-der/release/scripts/doctor.sh
```

View logs:

```bash
journalctl -u spy-der-market.service -f
journalctl -u spy-der-engine.service -f
journalctl -u spy-der-dashboard.service -f
```

## Service map

```text
Tradier / local demo
        │
        ▼
Market collector ──► immutable SQLite + JSONL tapes
        │
        ├──► constituent and SPY IV context
        ▼
Feature engine ──► T+15 forecast ──► candidate structures
        │                                  │
        ▼                                  ▼
Confirmation/audit                    Risk controller
        │                                  │
        ▼                                  ▼
Dojo/governance                  Paper or guarded execution
        │                                  │
        └──────────────► Command Center ◄──┘
                              │
                              ▼
                  Daily Google Drive backup
```

## Data preservation

The suite uses new v2 databases:

```text
/var/lib/spy-der/journal/suite-v2.db
/var/lib/spy-der/dashboard/command-center-v2.sqlite
```

It does not overwrite the prior `journal.db` or `/var/lib/zerodte/prediction_store.sqlite`. The backup service includes all recognized SQLite databases and all non-database files under both data roots.

## Production lock

Production submission requires all of the following:

- Production Tradier token and account ID
- `tradier.environment: production`
- `trading.enabled: true`
- `trading.paper_mode: false`
- `trading.submit_orders: true`
- `/etc/spy-der/PRODUCTION_UNLOCKED`
- Maximum contract setting no greater than one

The supplied production-unlock script requires an explicit confirmation phrase. It should not be used until sandbox execution, fills, cancel behavior, position reconciliation and forced-flat behavior have been verified on the actual account.

## Documentation

- [Build and deploy](docs/BUILD_AND_DEPLOY.md)
- [Installation](docs/INSTALL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations runbook](docs/OPERATIONS.md)
- [Tradier configuration](docs/TRADIER_SETUP.md)
- [Backup and restore](docs/BACKUP_RESTORE.md)
- [Security model](docs/SECURITY.md)
- [Data model](docs/DATA_MODEL.md)
- [Audit and learning process](docs/AUDIT_CONTROL_PROCESS.md)
- [Complete component inventory](docs/COMPONENT_INVENTORY.md)
- [Upgrade and rollback](docs/UPGRADE.md)
- [Build verification](BUILD_REPORT.md)

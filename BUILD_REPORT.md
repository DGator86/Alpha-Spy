# Build Verification Report

## Release

- Product: SPY Constituent Alpha Suite
- Version: 2.0.0
- Build date: 2026-08-06
- Target platform: Ubuntu 24.04 LTS, single VPS
- Python requirement: 3.11 or newer
- Installation posture: sandbox, paper mode, order submission disabled, production sentinel absent

## Included system

This release contains the complete source tree, bundled application wheel, Ubuntu installer, systemd service topology, secure browser GUI, local decision API, market and option-data collection, constituent feature and forecasting engine, defined-risk strategy generator, risk controller, guarded execution workflow, settlement monitor, immutable T+15 confirmation tape, model-governance Dojo, Google Drive backup tooling, research framework, tests, operating documentation, upgrade and rollback procedures, and a standalone GUI preview.

The installer preserves existing data under `/var/lib/spy-der` and `/var/lib/zerodte`, preserves the prior `/opt/spy-der` installation and configuration, and creates isolated v2 database files rather than overwriting recognized legacy databases.

## Automated verification completed

### Source tests

```text
27 passed
```

The suite tests cover runtime configuration, SQLite schemas, demo market ingestion, feature generation, immutable prediction creation, defined-risk candidate construction, Tradier quote normalization, timestamps, butterflies, fail-closed live execution, confirmation maturity, post-target snapshot selection, counterfactual outcomes, Dojo reporting, pricing, covariance, lead/lag, attribution, scanner behavior, empirical policies, strategy tournaments, and professional exits.

### Static validation

- Python compilation: passed for `src/`, `tests/`, and `examples/`
- Dashboard JavaScript syntax: passed with `node --check`
- Installer and utility shell syntax: passed with `bash -n`
- Systemd units and timers: passed with `systemd-analyze verify`

### Built wheel

```text
spy_constituent_alpha_suite-2.0.0-py3-none-any.whl
SHA-256: adfed16c5025ed744fd1884711df35d2a4dade5783b52104b3399b972d2fd50b
```

The final wheel was installed into an isolated target and exercised independently of the source tree.

### Installed-wheel smoke test

The wheel smoke test verified:

- Configuration load: passed
- Database initialization and `PRAGMA quick_check`: `ok`
- Demo market cycle: passed
- Prediction/decision cycle: passed
- Dashboard API startup: passed
- Decision API startup: passed
- Dashboard health endpoint: passed
- Decision health endpoint: passed
- Dashboard state without token: HTTP 401
- Dashboard state with view token: HTTP 200
- Administrative command with view token: HTTP 403
- Administrative command with admin token: HTTP 200
- GUI command queue to engine: completed
- `PAUSE_NEW_ENTRIES` control applied before decision processing: passed
- Runtime version: 2.0.0
- Demo audit health: GREEN
- Strategy matrix populated: six eligible or shadow rows in the smoke state

### Backup smoke test

The Google Drive backup program was exercised with a temporary live SQLite database and a controlled remote adapter. The test verified:

- Rclone preflight
- Database detection and temporary-space calculation
- SQLite Online Backup API snapshot
- Snapshot progress reporting
- `PRAGMA quick_check=ok`
- Zstandard compression
- Dated database upload path
- `latest` database copy
- Incremental non-database raw-file copy
- Database exclusion from raw-data copy
- Completion manifest with `result=SUCCESS`
- Lock and cleanup behavior

### GUI verification

- Standalone HTML preview generated from the final GUI source
- Chromium headless render: passed
- Browser console errors: none
- Page errors: none
- Screenshot dimensions: 1920 × 1080
- Command Center screenshot SHA-256: `dd380d25c9f6b3bfbbb4859d07fd9d041fe68bf40711e529f849f626ad5118fc`
- Standalone preview SHA-256: `a9e609f18f960bab7cbbee20191d899a2c671f2f39afe7eee82a25c8d1159897`

## Installed service topology

The installer deploys and manages:

- `spy-der-market.service`
- `spy-der-engine.service`
- `spy-der-confirmation.service`
- `spy-der-settlement.service`
- `spy-der-decision.service`
- `spy-der-dashboard.service`
- `spy-der-dojo.service`
- `spy-der-dojo.timer`
- `spy-der-backup.service`
- `spy-der-backup.timer`
- `spy-der.target`

The dashboard and decision API bind only to `127.0.0.1`. The intended access path is an SSH tunnel. View, administrator, and ingestion credentials are separate.

## Safety posture

The supplied configuration is fail-closed:

- Tradier sandbox environment
- Trading disabled
- Paper mode enabled
- Order submission disabled
- Broker preview required
- One contract maximum
- One new trade per day maximum
- $100 maximum modeled trade risk
- $200 managed daily-loss limit
- One managed position at a time
- Data-integrity and stale-data entry blocks
- Time-gated entries
- Forced-flat target at 15:55 Eastern
- Separate production sentinel required

Production submission cannot be enabled only through the GUI. It requires an explicit configuration change, production account credentials, paper mode removal, order submission enablement, and `/etc/spy-der/PRODUCTION_UNLOCKED`.

## Deliberate limitations and unverified items

This build was not installed on the user's actual VPS from this environment. It was not connected to the user's real Tradier account, and no real or sandbox brokerage order was submitted. Consequently, account-specific permissions, live quote entitlements, broker order-state transitions, fill behavior, cancel/replace behavior, and actual forced-flat behavior remain to be verified on the target account before any production unlock.

The installer and service units were validated statically and through local process/API smoke tests, not by rebooting the user's VPS. The Google Drive backup logic was fully exercised against a controlled adapter; the user's already-authorized `gdrive:` remote must still be checked by the installer on the VPS. The installer enables the 5:00 PM Eastern backup timer only when that root-owned remote responds successfully.

The constituent universe uses an iShares IVV holdings source with a bundled fallback. Availability, format changes, and market-data coverage remain external dependencies. The system records data integrity and blocks trading when configured coverage requirements are not satisfied.

## Installation command

```bash
cd /root
tar -xzf spy-constituent-alpha-suite-v2.0.0.tar.gz
cd spy-constituent-alpha-suite-v2.0.0
sudo bash install.sh
```

After installation, credentials and tunnel instructions are written to:

```text
/root/spy-der-credentials.txt
```

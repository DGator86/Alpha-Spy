# VPS Installation

## Supported target

- Ubuntu 24.04 LTS
- Root or sudo access
- At least 4 CPU cores recommended
- At least 8 GB RAM recommended
- Sufficient disk for the existing data roots plus temporary SQLite snapshots
- Outbound HTTPS access to Tradier, iShares and Google Drive

## Pre-install inventory

```bash
df -h / /var/lib /var/tmp
free -h
pgrep -af 'alpha-spy'
systemctl list-units --all | grep -E 'alpha-spy'
rclone about gdrive:
```

The installer performs a fresh installation. It rebuilds `/opt/alpha-spy` and regenerates `/etc/alpha-spy`, leaving trading data under `/var/lib/alpha-spy` in place.

## Installation

```bash
tar -xzf alpha-spy-v3.0.0.tar.gz
cd alpha-spy-v3.0.0
sudo bash install.sh
```

## Generated files

```text
/etc/alpha-spy/config.yaml
/etc/alpha-spy/secrets.env
/etc/alpha-spy/backup.env
/etc/alpha-spy/universe.csv
/root/alpha-spy-credentials.txt
```

## Installed software

```text
/opt/alpha-spy/release
/opt/alpha-spy/venv
/usr/local/sbin/alpha-spy-backup
```

## Installed services

```text
alpha-spy-market.service
alpha-spy-engine.service
alpha-spy-confirmation.service
alpha-spy-settlement.service
alpha-spy-decision.service
alpha-spy-dashboard.service
alpha-spy-dojo.timer
alpha-spy-validation.timer
alpha-spy-event-calendar.timer
alpha-spy-backup.timer
alpha-spy.target
```

## First checks

```bash
systemctl status alpha-spy.target --no-pager
systemctl list-timers --all | grep alpha-spy
/opt/alpha-spy/release/scripts/doctor.sh
```

## Browser access

The services intentionally listen on loopback only. Use an SSH tunnel:

```powershell
ssh -L 8788:127.0.0.1:8788 -L 8787:127.0.0.1:8787 root@YOUR_VPS_IP
```

The dashboard is `http://127.0.0.1:8788` and the local decision API is `http://127.0.0.1:8787`.

## Rollback

Stop Alpha-SPY:

```bash
systemctl disable --now alpha-spy.target alpha-spy-dojo.timer alpha-spy-validation.timer alpha-spy-event-calendar.timer alpha-spy-backup.timer
```

Each install rebuilds `/opt/alpha-spy` from the release archive; see [UPGRADE.md](UPGRADE.md) for rollback.

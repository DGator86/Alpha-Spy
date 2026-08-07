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
pgrep -af 'spy-der|zerodte'
systemctl list-units --all | grep -E 'spy-der|zerodte'
rclone about gdrive:
```

The installer preserves existing data and creates a timestamped copy of prior configuration. It also moves the previous `/opt/spy-der` tree to `/opt/spy-der.pre-v2-<timestamp>`.

## Installation

```bash
tar -xzf spy-constituent-alpha-suite-v2.0.0.tar.gz
cd spy-constituent-alpha-suite-v2.0.0
sudo bash install.sh
```

## Generated files

```text
/etc/spy-der/config.yaml
/etc/spy-der/secrets.env
/etc/spy-der/backup.env
/etc/spy-der/universe.csv
/root/spy-der-credentials.txt
```

## Installed software

```text
/opt/spy-der/release
/opt/spy-der/venv
/usr/local/sbin/spy-der-drive-backup
```

## Installed services

```text
spy-der-market.service
spy-der-engine.service
spy-der-confirmation.service
spy-der-settlement.service
spy-der-decision.service
spy-der-dashboard.service
spy-der-dojo.timer
spy-der-backup.timer
spy-der.target
```

## First checks

```bash
systemctl status spy-der.target --no-pager
systemctl list-timers --all | grep spy-der
/opt/spy-der/release/scripts/doctor.sh
```

## Browser access

The services intentionally listen on loopback only. Use an SSH tunnel:

```powershell
ssh -L 8788:127.0.0.1:8788 -L 8787:127.0.0.1:8787 root@YOUR_VPS_IP
```

The dashboard is `http://127.0.0.1:8788` and the local decision API is `http://127.0.0.1:8787`.

## Rollback

Stop v2:

```bash
systemctl disable --now spy-der.target spy-der-dojo.timer spy-der-backup.timer
```

The prior program tree is preserved under `/opt/spy-der.pre-v2-<timestamp>`. Prior configuration is preserved under `/var/backups/spy-der-pre-v2-<timestamp>`.

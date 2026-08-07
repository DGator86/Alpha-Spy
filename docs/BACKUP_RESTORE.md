# Google Drive Backup and Restore

## Schedule

The timer runs every day at 5:00 PM America/New_York:

```bash
systemctl list-timers --all | grep alpha-spy-backup
systemd-analyze calendar '*-*-* 17:00:00 America/New_York'
```

## Backup contents

- Every recognized SQLite database under `/var/lib/alpha-spy`
- Verified online snapshots compressed with Zstandard
- Dated and `latest` database copies
- Incremental copies of non-database raw data
- A manifest for each run

The backup uses `rclone copy`, not destructive synchronization. It does not delete Google Drive files.

## Run manually

```bash
systemctl start alpha-spy-backup.service
journalctl -u alpha-spy-backup.service -f
```

## Remote layout

```text
SPY Trading Backups/<hostname>/
  database-snapshots/
    daily/YYYY-MM-DD/
    latest/
  raw/
    alpha-spy/
  manifests/
```

## Restore a database

1. Stop the suite.
2. Download the selected `.zst` file.
3. Decompress to a new path.
4. Run `PRAGMA quick_check`.
5. Preserve the current local database before replacing it.
6. Correct ownership and permissions.
7. Start the suite and run the doctor.

Example:

```bash
systemctl stop alpha-spy.target
rclone copyto \
  'gdrive:SPY Trading Backups/srv1575978/database-snapshots/latest/alpha-spy/journal/alpha-spy.db.zst' \
  /var/tmp/alpha-spy.db.zst
zstd -d -f /var/tmp/alpha-spy.db.zst -o /var/tmp/alpha-spy.db
sqlite3 /var/tmp/alpha-spy.db 'PRAGMA quick_check;'
cp -a /var/lib/alpha-spy/journal/alpha-spy.db /var/backups/alpha-spy-before-restore.db
install -o alphaspy -g alphaspy -m 600 /var/tmp/alpha-spy.db /var/lib/alpha-spy/journal/alpha-spy.db
systemctl start alpha-spy.target
/opt/alpha-spy/release/scripts/doctor.sh
```

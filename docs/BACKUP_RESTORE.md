# Google Drive Backup and Restore

## Schedule

The timer runs every day at 5:00 PM America/New_York:

```bash
systemctl list-timers --all | grep spy-der-backup
systemd-analyze calendar '*-*-* 17:00:00 America/New_York'
```

## Backup contents

- Every recognized SQLite database under `/var/lib/spy-der` and `/var/lib/zerodte`
- Verified online snapshots compressed with Zstandard
- Dated and `latest` database copies
- Incremental copies of non-database raw data
- A manifest for each run

The backup uses `rclone copy`, not destructive synchronization. It does not delete Google Drive files.

## Run manually

```bash
systemctl start spy-der-backup.service
journalctl -u spy-der-backup.service -f
```

## Remote layout

```text
SPY Trading Backups/<hostname>/
  database-snapshots/
    daily/YYYY-MM-DD/
    latest/
  raw/
    spy-der/
    zerodte/
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
systemctl stop spy-der.target
rclone copyto \
  'gdrive:SPY Trading Backups/srv1575978/database-snapshots/latest/spy-der/journal/suite-v2.db.zst' \
  /var/tmp/suite-v2.db.zst
zstd -d -f /var/tmp/suite-v2.db.zst -o /var/tmp/suite-v2.db
sqlite3 /var/tmp/suite-v2.db 'PRAGMA quick_check;'
cp -a /var/lib/spy-der/journal/suite-v2.db /var/backups/suite-v2-before-restore.db
install -o spyder -g spyder -m 600 /var/tmp/suite-v2.db /var/lib/spy-der/journal/suite-v2.db
systemctl start spy-der.target
/opt/spy-der/release/scripts/doctor.sh
```

# Upgrade and Rollback

Alpha-SPY is a standalone product. There is no migration path from any other
application: the installer performs a fresh installation and touches only
Alpha-SPY-owned paths, units and the `alphaspy` service account.

## Upgrading Alpha-SPY

Upgrading is the same operation as installing. Transfer the new release,
verify it, and run the installer:

```bash
cd /root
sha256sum -c alpha-spy-v<version>.tar.gz.sha256
tar -xzf alpha-spy-v<version>.tar.gz
cd alpha-spy-v<version>
bash scripts/verify_release.sh ../alpha-spy-v<version>.tar.gz
sudo bash install.sh
```

The installer:

1. Stops the Alpha-SPY services and timers.
2. Replaces `/opt/alpha-spy/release` and rebuilds `/opt/alpha-spy/venv`.
3. Rewrites `/etc/alpha-spy/config.yaml` and `/etc/alpha-spy/universe.csv`.
4. Issues fresh view, administrator and ingestion tokens.
5. Removes `/etc/alpha-spy/PRODUCTION_UNLOCKED`, so an upgrade always lands
   locked.
6. Starts everything in sandbox/paper mode with order submission disabled.

Trading data under `/var/lib/alpha-spy` is left in place — the installer never
deletes it — but configuration and credentials are regenerated. Re-run
`configure_tradier.sh` after an upgrade, and take a backup first:

```bash
sudo /opt/alpha-spy/release/scripts/backup_now.sh
```

## Rollback

Stop the suite:

```bash
sudo systemctl disable --now alpha-spy.target alpha-spy-dojo.timer alpha-spy-backup.timer
```

Then install the release you want to return to, exactly as above. Because each
install rebuilds `/opt/alpha-spy` from the archive, rolling back means
installing the older archive; there is no side-by-side copy to restore from.

Restoring data is covered in [BACKUP_RESTORE.md](BACKUP_RESTORE.md).

Do not copy an older executable over a running installation. Stop the target
first.

## Removing Alpha-SPY

```bash
sudo /opt/alpha-spy/release/scripts/uninstall.sh
```

This removes the software, units and backup tooling. It preserves
`/var/lib/alpha-spy` and `/etc/alpha-spy` so data and configuration survive;
delete those directories by hand if you want the host clean.

# Upgrade and Existing-System Preservation

The installer is designed for the current `srv1575978` layout but uses hostname-based backup paths and standard Linux locations, so it can also install on another Ubuntu VPS.

During installation it:

1. Stops and disables legacy SPY timers that could duplicate work.
2. Stops prior `/opt/spy-der/venv/bin/spy-der` processes.
3. Moves the existing `/opt/spy-der` directory to a timestamped side-by-side backup.
4. Copies `/etc/spy-der/config.yaml` and `/etc/spy-der/secrets.env` into `/var/backups/spy-der-pre-v2-*`.
5. Preserves `/var/lib/spy-der` and `/var/lib/zerodte` in place.
6. Uses new v2 database filenames so prior SQLite databases are not overwritten.
7. Recovers recognizable Tradier credentials and dashboard tokens when present.
8. Installs and starts the new services in sandbox/paper mode.

The installer never deletes trading data. The uninstall script also preserves `/var/lib/spy-der` and `/etc/spy-der`.

## Rollback

To stop the new suite:

```bash
systemctl disable --now spy-der.target spy-der-dojo.timer spy-der-backup.timer
```

The former installation remains under a directory similar to:

```text
/opt/spy-der.pre-v2-YYYYMMDDTHHMMSSZ
```

The former configuration backup remains under:

```text
/var/backups/spy-der-pre-v2-YYYYMMDDTHHMMSSZ
```

Do not restore an old executable over the new installation while the new services are running.

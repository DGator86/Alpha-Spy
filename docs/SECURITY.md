# Security Model

## Network

- Dashboard: `127.0.0.1:8788`
- Decision API: `127.0.0.1:8787`
- No public listener is installed.
- SSH tunneling is the default access method.

## Credentials

- Tradier and dashboard tokens are stored in `/etc/spy-der/secrets.env`.
- The file is readable by root and the `spyder` group only.
- View, administrator and ingestion tokens are separate.
- Browser tokens use session storage rather than persistent local storage.

## Process hardening

Systemd units use:

- Restricted service account
- `NoNewPrivileges=true`
- Private temporary directories
- Read-only system filesystem with explicit writable data paths
- Restrictive umask
- Automatic restart

## Trading safety

Live submission is blocked unless every production condition passes. The GUI cannot directly call Tradier order endpoints; it queues commands for the engine. A flatten request requires the exact confirmation phrase `FLATTEN_SPY_ALPHA_POSITION`.

## Backups

The backup service runs as root only because the rclone token is stored in root's configuration and the backup must read all preserved legacy databases. Temporary files use a restrictive umask and are deleted after completion.

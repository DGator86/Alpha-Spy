# Operations Runbook

## Overall status

```bash
/opt/spy-der/release/scripts/status.sh
```

## Service commands

```bash
systemctl start spy-der.target
systemctl stop spy-der.target
systemctl restart spy-der.target
```

Restart one component:

```bash
systemctl restart spy-der-market.service
systemctl restart spy-der-engine.service
systemctl restart spy-der-dashboard.service
```

## Logs

```bash
journalctl -u spy-der-market.service -n 200 --no-pager
journalctl -u spy-der-engine.service -n 200 --no-pager
journalctl -u spy-der-confirmation.service -n 200 --no-pager
journalctl -u spy-der-settlement.service -n 200 --no-pager
journalctl -u spy-der-dashboard.service -n 200 --no-pager
```

Follow live logs:

```bash
journalctl -u spy-der-engine.service -f
```

## Pause and resume entries

From the GUI, use **Pause Entries**. The engine writes the state to `control_state` and continues collecting/confirming data.

CLI equivalent:

```bash
sqlite3 /var/lib/spy-der/journal/suite-v2.db \
  "INSERT INTO control_state(key,value,updated_at) VALUES('entries_paused','true',datetime('now')) ON CONFLICT(key) DO UPDATE SET value='true',updated_at=datetime('now');"
```

Resume by changing the value to `false` or using the GUI.

## Health endpoints

```bash
curl -s http://127.0.0.1:8787/health | python3 -m json.tool
curl -s http://127.0.0.1:8788/api/v1/health | python3 -m json.tool
```

## Database checks

```bash
sqlite3 /var/lib/spy-der/journal/suite-v2.db 'PRAGMA quick_check;'
sqlite3 /var/lib/spy-der/dashboard/command-center-v2.sqlite 'PRAGMA quick_check;'
```

Do not copy a live SQLite database directly. Use the online-backup script or SQLite backup API.

## Support bundle

```bash
sudo /opt/spy-der/release/scripts/export_support_bundle.sh
```

This exports configuration with secrets redacted, service status, recent logs, schemas, disk status and checksums. It does not include API tokens.

# Operations Runbook

## Overall status

```bash
/opt/alpha-spy/release/scripts/status.sh
```

## Service commands

```bash
systemctl start alpha-spy.target
systemctl stop alpha-spy.target
systemctl restart alpha-spy.target
```

Restart one component:

```bash
systemctl restart alpha-spy-market.service
systemctl restart alpha-spy-engine.service
systemctl restart alpha-spy-dashboard.service
```

## Logs

```bash
journalctl -u alpha-spy-market.service -n 200 --no-pager
journalctl -u alpha-spy-engine.service -n 200 --no-pager
journalctl -u alpha-spy-confirmation.service -n 200 --no-pager
journalctl -u alpha-spy-settlement.service -n 200 --no-pager
journalctl -u alpha-spy-dashboard.service -n 200 --no-pager
```

Follow live logs:

```bash
journalctl -u alpha-spy-engine.service -f
```

## Pause and resume entries

From the GUI, use **Pause Entries**. The engine writes the state to `control_state` and continues collecting/confirming data.

CLI equivalent:

```bash
sqlite3 /var/lib/alpha-spy/journal/alpha-spy.db \
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
sqlite3 /var/lib/alpha-spy/journal/alpha-spy.db 'PRAGMA quick_check;'
sqlite3 /var/lib/alpha-spy/dashboard/command-center.sqlite 'PRAGMA quick_check;'
```

Do not copy a live SQLite database directly. Use the online-backup script or SQLite backup API.

## Support bundle

```bash
sudo /opt/alpha-spy/release/scripts/export_support_bundle.sh
```

This exports configuration with secrets redacted, service status, recent logs, schemas, disk status and checksums. It does not include API tokens.

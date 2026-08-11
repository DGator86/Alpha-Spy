#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash $0" >&2; exit 1; }

CONFIG="${ALPHA_SPY_CONFIG:-/etc/alpha-spy/config.yaml}"
PYTHON="${ALPHA_SPY_PYTHON:-/opt/alpha-spy/venv/bin/python}"
CLI="${ALPHA_SPY_CLI:-/opt/alpha-spy/venv/bin/alpha-spy}"
[[ -x "$PYTHON" ]] || PYTHON=python3
[[ -x "$CLI" ]] || CLI=alpha-spy

SOURCE="${ALPHA_SPY_EVENT_CALENDAR_SOURCE:-${1:-}}"
if [[ -z "$SOURCE" ]]; then
  read -r -p "Authoritative event-calendar HTTPS URL or local JSON path: " SOURCE
fi
[[ -n "$SOURCE" ]] || { echo "Event calendar source is required" >&2; exit 1; }

"$PYTHON" - "$CONFIG" "$SOURCE" <<'PY'
from pathlib import Path
import sys
import yaml
path=Path(sys.argv[1])
data=yaml.safe_load(path.read_text()) or {}
data.setdefault('context', {})['event_calendar_url']=sys.argv[2]
data['context']['event_calendar_enabled']=True
data['context']['event_calendar_required']=True
path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
PY
chmod 640 "$CONFIG"
chown root:alphaspy "$CONFIG"

set -a
[[ -f /etc/alpha-spy/secrets.env ]] && source /etc/alpha-spy/secrets.env
set +a
"$CLI" --config "$CONFIG" event-calendar-refresh --source "$SOURCE" >/tmp/alpha-spy-event-calendar.json
systemctl enable --now alpha-spy-event-calendar.timer
systemctl restart alpha-spy-engine.service

echo "Event calendar validated, installed, archived, and refresh timer enabled."
cat /tmp/alpha-spy-event-calendar.json
rm -f /tmp/alpha-spy-event-calendar.json

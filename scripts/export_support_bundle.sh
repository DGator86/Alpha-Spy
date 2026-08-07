#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }

# PyYAML ships as a dependency of the installed wheel, not of the system
# interpreter, and install_vps.sh never adds python3-yaml. Prefer the suite's
# virtualenv so this keeps working on a minimal Ubuntu image.
PYTHON="${ALPHA_SPY_PYTHON:-/opt/alpha-spy/venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON=python3
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d /var/tmp/alpha-spy-support.XXXXXX)"
OUT="/root/alpha-spy-support-$STAMP.tar.gz"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK"

"$PYTHON" - <<'PY' >"$WORK/config-redacted.yaml"
from pathlib import Path
import yaml
p=Path('/etc/alpha-spy/config.yaml')
d=yaml.safe_load(p.read_text()) if p.exists() else {}
if isinstance(d,dict):
    if isinstance(d.get('tradier'),dict): d['tradier']['access_token']='***REDACTED***'
    if isinstance(d.get('dashboard'),dict):
        for k in ('view_token','admin_token','ingest_token'): d['dashboard'][k]='***REDACTED***'
print(yaml.safe_dump(d,sort_keys=False))
PY

systemctl status alpha-spy.target --no-pager >"$WORK/target-status.txt" 2>&1 || true
systemctl list-timers --all >"$WORK/timers.txt" 2>&1 || true
journalctl -u 'alpha-spy-*' --since '24 hours ago' --no-pager -n 5000 >"$WORK/recent-logs.txt" 2>&1 || true
df -h >"$WORK/disk.txt"
free -h >"$WORK/memory.txt"
pgrep -af 'alpha-spy' >"$WORK/processes.txt" || true
rclone version >"$WORK/rclone-version.txt" 2>&1 || true
rclone about gdrive: >"$WORK/gdrive-about.txt" 2>&1 || true

for db in /var/lib/alpha-spy/journal/alpha-spy.db /var/lib/alpha-spy/dashboard/command-center.sqlite; do
  [[ -f "$db" ]] || continue
  name="$(basename "$db")"
  sqlite3 "$db" '.schema' >"$WORK/$name-schema.sql" 2>&1 || true
  sqlite3 "$db" 'PRAGMA quick_check;' >"$WORK/$name-quick-check.txt" 2>&1 || true
  stat "$db" >"$WORK/$name-stat.txt"
done

sha256sum /opt/alpha-spy/release/dist/*.whl >"$WORK/wheel-checksums.txt" 2>/dev/null || true
tar -C "$WORK" -czf "$OUT" .
echo "$OUT"

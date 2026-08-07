#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d /var/tmp/spy-der-support.XXXXXX)"
OUT="/root/spy-der-support-$STAMP.tar.gz"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK"

python3 - <<'PY' >"$WORK/config-redacted.yaml"
from pathlib import Path
import yaml
p=Path('/etc/spy-der/config.yaml')
d=yaml.safe_load(p.read_text()) if p.exists() else {}
if isinstance(d,dict):
    if isinstance(d.get('tradier'),dict): d['tradier']['access_token']='***REDACTED***'
    if isinstance(d.get('dashboard'),dict):
        for k in ('view_token','admin_token','ingest_token'): d['dashboard'][k]='***REDACTED***'
print(yaml.safe_dump(d,sort_keys=False))
PY

systemctl status spy-der.target --no-pager >"$WORK/target-status.txt" 2>&1 || true
systemctl list-timers --all >"$WORK/timers.txt" 2>&1 || true
journalctl -u 'spy-der-*' --since '24 hours ago' --no-pager -n 5000 >"$WORK/recent-logs.txt" 2>&1 || true
df -h >"$WORK/disk.txt"
free -h >"$WORK/memory.txt"
pgrep -af 'spy-der|zerodte' >"$WORK/processes.txt" || true
rclone version >"$WORK/rclone-version.txt" 2>&1 || true
rclone about gdrive: >"$WORK/gdrive-about.txt" 2>&1 || true

for db in /var/lib/spy-der/journal/suite-v2.db /var/lib/spy-der/dashboard/command-center-v2.sqlite; do
  [[ -f "$db" ]] || continue
  name="$(basename "$db")"
  sqlite3 "$db" '.schema' >"$WORK/$name-schema.sql" 2>&1 || true
  sqlite3 "$db" 'PRAGMA quick_check;' >"$WORK/$name-quick-check.txt" 2>&1 || true
  stat "$db" >"$WORK/$name-stat.txt"
done

sha256sum /opt/spy-der/release/dist/*.whl >"$WORK/wheel-checksums.txt" 2>/dev/null || true
tar -C "$WORK" -czf "$OUT" .
echo "$OUT"

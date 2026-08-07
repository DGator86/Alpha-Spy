#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }

echo '=== Platform ==='
. /etc/os-release
printf '%s %s\n' "$NAME" "$VERSION_ID"
uname -a

echo
echo '=== Capacity ==='
df -h / /var/lib /var/tmp
free -h

echo
echo '=== Existing processes ==='
pgrep -af 'alpha-spy' || true

echo
echo '=== Existing databases ==='
find /var/lib/alpha-spy -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -printf '%s %p\n' 2>/dev/null | sort -n || true

echo
echo '=== Google Drive ==='
if command -v rclone >/dev/null && rclone about gdrive:; then
  echo 'gdrive remote: OK'
else
  echo 'gdrive remote: unavailable; installer will leave backup timer disabled'
fi

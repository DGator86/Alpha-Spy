#!/usr/bin/env bash
set -Eeuo pipefail

echo '=== Alpha-SPY ==='
/opt/alpha-spy/venv/bin/alpha-spy --config /etc/alpha-spy/config.yaml doctor || true

echo
echo '=== Services ==='
for unit in market engine confirmation settlement decision dashboard; do
  printf '%-30s %s\n' "alpha-spy-$unit.service" "$(systemctl is-active "alpha-spy-$unit.service" 2>/dev/null || true)"
done

echo
echo '=== Timers ==='
systemctl list-timers --all | grep -E 'alpha-spy-(dojo|backup)' || true

echo
echo '=== Local APIs ==='
curl -fsS http://127.0.0.1:8787/health || true; echo
curl -fsS http://127.0.0.1:8788/api/v1/health || true; echo

echo
echo '=== Disk and memory ==='
df -h / /var/lib /var/tmp
free -h

echo
echo '=== Recent critical logs ==='
journalctl -p err -u 'alpha-spy-*' --since '24 hours ago' --no-pager -n 50 || true

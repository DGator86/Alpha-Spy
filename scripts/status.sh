#!/usr/bin/env bash
set -Eeuo pipefail

echo '=== SPY Constituent Alpha Suite ==='
/opt/spy-der/venv/bin/spy-der --config /etc/spy-der/config.yaml doctor || true

echo
echo '=== Services ==='
for unit in market engine confirmation settlement decision dashboard; do
  printf '%-30s %s\n' "spy-der-$unit.service" "$(systemctl is-active "spy-der-$unit.service" 2>/dev/null || true)"
done

echo
echo '=== Timers ==='
systemctl list-timers --all | grep -E 'spy-der-(dojo|backup)' || true

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
journalctl -p err -u 'spy-der-*' --since '24 hours ago' --no-pager -n 50 || true

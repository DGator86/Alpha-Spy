#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }
systemctl disable --now alpha-spy.target alpha-spy-dojo.timer alpha-spy-backup.timer 2>/dev/null || true
rm -f /etc/systemd/system/alpha-spy*.service /etc/systemd/system/alpha-spy*.timer /etc/systemd/system/alpha-spy.target
systemctl daemon-reload
rm -rf /opt/alpha-spy
rm -f /usr/local/sbin/alpha-spy-backup
echo "Software removed. /var/lib/alpha-spy and /etc/alpha-spy were preserved."

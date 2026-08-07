#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }
systemctl disable --now spy-der.target spy-der-dojo.timer spy-der-backup.timer 2>/dev/null || true
rm -f /etc/systemd/system/spy-der*.service /etc/systemd/system/spy-der*.timer /etc/systemd/system/spy-der.target
systemctl daemon-reload
rm -rf /opt/spy-der
rm -f /usr/local/sbin/spy-der-drive-backup
echo "Software removed. /var/lib/spy-der and /etc/spy-der were preserved."

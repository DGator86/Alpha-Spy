#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }
systemctl start alpha-spy-backup.service
journalctl -u alpha-spy-backup.service -f

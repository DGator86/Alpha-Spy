#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }
systemctl start spy-der-backup.service
journalctl -u spy-der-backup.service -f

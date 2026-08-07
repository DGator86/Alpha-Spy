#!/usr/bin/env bash
set -Eeuo pipefail
set -a; source /etc/spy-der/secrets.env; set +a
/opt/spy-der/venv/bin/spy-der --config /etc/spy-der/config.yaml doctor
systemctl --no-pager --full status spy-der.target || true
systemctl list-timers --all | grep -E 'spy-der-(dojo|backup)' || true
curl -fsS http://127.0.0.1:8787/health; echo
curl -fsS http://127.0.0.1:8788/api/v1/health; echo

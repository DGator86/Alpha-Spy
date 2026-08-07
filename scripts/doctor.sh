#!/usr/bin/env bash
set -Eeuo pipefail
set -a; source /etc/alpha-spy/secrets.env; set +a
/opt/alpha-spy/venv/bin/alpha-spy --config /etc/alpha-spy/config.yaml doctor
systemctl --no-pager --full status alpha-spy.target || true
systemctl list-timers --all | grep -E 'alpha-spy-(dojo|backup)' || true
curl -fsS http://127.0.0.1:8787/health; echo
curl -fsS http://127.0.0.1:8788/api/v1/health; echo

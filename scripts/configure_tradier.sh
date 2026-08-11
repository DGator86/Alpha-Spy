#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

read -rsp "Tradier PRODUCTION market-data API token: " MARKET_TOKEN; echo
read -rsp "Tradier SANDBOX paper-trading API token: " EXEC_TOKEN; echo
read -rp "Tradier SANDBOX virtual account ID: " EXEC_ACCOUNT

[[ -n "$MARKET_TOKEN" ]] || { echo "Production market-data token is required"; exit 1; }
[[ -n "$EXEC_TOKEN" ]] || { echo "Sandbox execution token is required"; exit 1; }
[[ -n "$EXEC_ACCOUNT" ]] || { echo "Sandbox account ID is required"; exit 1; }

python3 - "$MARKET_TOKEN" "$EXEC_TOKEN" "$EXEC_ACCOUNT" <<'PY'
from pathlib import Path
import shlex
import sys

p = Path('/etc/alpha-spy/secrets.env')
updates = {
    'TRADIER_MARKET_ACCESS_TOKEN': sys.argv[1],
    'TRADIER_STREAM_ENABLED': 'true',
    'TRADIER_EXECUTION_ACCESS_TOKEN': sys.argv[2],
    'TRADIER_EXECUTION_ACCOUNT_ID': sys.argv[3],
    'TRADIER_EXECUTION_ENVIRONMENT': 'sandbox',
    'ALPHA_SPY_TRADING_ENABLED': 'true',
    'ALPHA_SPY_SUBMIT_ORDERS': 'true',
    'ALPHA_SPY_PAPER_MODE': 'true',
}
lines = p.read_text().splitlines() if p.exists() else []
seen = set()
out = []
for line in lines:
    key = line.split('=', 1)[0] if '=' in line else ''
    if key in updates:
        out.append(f'{key}={shlex.quote(updates[key])}')
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={shlex.quote(value)}')
p.write_text('\n'.join(out) + '\n', encoding='utf-8')
PY
chmod 640 /etc/alpha-spy/secrets.env
chown root:alphaspy /etc/alpha-spy/secrets.env
systemctl restart alpha-spy.target
sleep 5
/opt/alpha-spy/venv/bin/alpha-spy --config /etc/alpha-spy/config.yaml doctor

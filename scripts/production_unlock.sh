#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

echo "This enables REAL order submission. Paper validation must be completed first."
read -rp "Type ENABLE_REAL_SPY_OPTIONS_TRADING: " CONFIRM
[[ "$CONFIRM" == "ENABLE_REAL_SPY_OPTIONS_TRADING" ]] || { echo "Not enabled"; exit 1; }

source /etc/alpha-spy/secrets.env
[[ -n "${TRADIER_PRODUCTION_EXECUTION_ACCESS_TOKEN:-}" ]] || { echo "Production execution token missing"; exit 1; }
[[ -n "${TRADIER_PRODUCTION_EXECUTION_ACCOUNT_ID:-}" ]] || { echo "Production execution account ID missing"; exit 1; }
[[ -f /etc/alpha-spy/PRODUCTION_APPROVED.json ]] || { echo "Production approval artifact missing"; exit 1; }

# Verify the paper-validation evidence and approval fingerprint *before* changing
# broker credentials or creating the production sentinel.
/opt/alpha-spy/venv/bin/python - <<'PY'
from alpha_spy.config import load_config
config = load_config('/etc/alpha-spy/config.yaml')
ok, reason = config.production_approval_status()
if not ok:
    raise SystemExit(f'Production approval invalid: {reason}')
print('Production approval evidence verified')
PY

python3 - "${TRADIER_PRODUCTION_EXECUTION_ACCESS_TOKEN}" "${TRADIER_PRODUCTION_EXECUTION_ACCOUNT_ID}" <<'PY'
from pathlib import Path
import shlex
import sys

p = Path('/etc/alpha-spy/secrets.env')
updates = {
    'TRADIER_EXECUTION_ACCESS_TOKEN': sys.argv[1],
    'TRADIER_EXECUTION_ACCOUNT_ID': sys.argv[2],
    'TRADIER_EXECUTION_ENVIRONMENT': 'production',
    'ALPHA_SPY_TRADING_ENABLED': 'true',
    'ALPHA_SPY_SUBMIT_ORDERS': 'true',
    'ALPHA_SPY_PAPER_MODE': 'false',
}
lines = p.read_text().splitlines()
out=[]; seen=set()
for line in lines:
    key=line.split('=',1)[0] if '=' in line else ''
    if key in updates:
        out.append(f'{key}={shlex.quote(updates[key])}'); seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={shlex.quote(value)}')
p.write_text('\n'.join(out)+'\n', encoding='utf-8')
PY

touch /etc/alpha-spy/PRODUCTION_UNLOCKED
chmod 600 /etc/alpha-spy/PRODUCTION_UNLOCKED
systemctl restart alpha-spy.target
sleep 5
/opt/alpha-spy/venv/bin/alpha-spy --config /etc/alpha-spy/config.yaml doctor

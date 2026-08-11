#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

# Fail closed first. Even if a later config/environment edit fails, the live
# production sentinel is already gone.
rm -f /etc/alpha-spy/PRODUCTION_UNLOCKED /etc/alpha-spy/PRODUCTION_APPROVED.json

PYTHON="${ALPHA_SPY_PYTHON:-/opt/alpha-spy/venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON=python3
"$PYTHON" - <<'PY'
from pathlib import Path
import yaml
p=Path('/etc/alpha-spy/config.yaml')
d=yaml.safe_load(p.read_text())
d['trading']['enabled']=False
d['trading']['paper_mode']=True
d['trading']['submit_orders']=False
p.write_text(yaml.safe_dump(d, sort_keys=False))
PY

"$PYTHON" - <<'PY'
from pathlib import Path
import shlex

p = Path('/etc/alpha-spy/secrets.env')
updates = {
    'ALPHA_SPY_TRADING_ENABLED': 'false',
    'ALPHA_SPY_SUBMIT_ORDERS': 'false',
    'ALPHA_SPY_PAPER_MODE': 'true',
}
lines = p.read_text().splitlines() if p.exists() else []
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
chmod 640 /etc/alpha-spy/secrets.env
chown root:alphaspy /etc/alpha-spy/secrets.env
systemctl restart alpha-spy.target
echo "Broker submission locked. Run configure_tradier.sh to re-enable sandbox paper execution."

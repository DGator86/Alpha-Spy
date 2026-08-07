#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

# PyYAML ships as a dependency of the installed wheel, not of the system
# interpreter, and install_vps.sh never adds python3-yaml. Prefer the suite's
# virtualenv so this keeps working on a minimal Ubuntu image.
PYTHON="${SPY_DER_PYTHON:-/opt/spy-der/venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON=python3
echo "This enables real order submission. Sandbox and paper testing must be completed first."
read -rp "Type ENABLE_REAL_SPY_OPTIONS_TRADING: " CONFIRM
[[ "$CONFIRM" == "ENABLE_REAL_SPY_OPTIONS_TRADING" ]] || { echo "Not enabled"; exit 1; }
source /etc/spy-der/secrets.env
[[ -n "${TRADIER_ACCESS_TOKEN:-}" && -n "${TRADIER_ACCOUNT_ID:-}" ]] || { echo "Tradier credentials missing"; exit 1; }
"$PYTHON" - <<'PY'
from pathlib import Path
import yaml
p=Path('/etc/spy-der/config.yaml')
d=yaml.safe_load(p.read_text())
d['tradier']['environment']='production'
d['trading']['enabled']=True
d['trading']['paper_mode']=False
d['trading']['submit_orders']=True
d['risk']['maximum_contracts']=1
p.write_text(yaml.safe_dump(d, sort_keys=False))
PY
touch /etc/spy-der/PRODUCTION_UNLOCKED
chmod 600 /etc/spy-der/PRODUCTION_UNLOCKED
"$PYTHON" - <<'PY'
from pathlib import Path
p=Path('/etc/spy-der/secrets.env')
s=p.read_text()
import re
s=re.sub(r'^TRADIER_ENVIRONMENT=.*$', 'TRADIER_ENVIRONMENT=production', s, flags=re.M)
p.write_text(s)
PY
systemctl restart spy-der.target
/opt/spy-der/venv/bin/spy-der --config /etc/spy-der/config.yaml doctor

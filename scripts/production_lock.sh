#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

# PyYAML ships as a dependency of the installed wheel, not of the system
# interpreter, and install_vps.sh never adds python3-yaml. Prefer the suite's
# virtualenv so this keeps working on a minimal Ubuntu image.
PYTHON="${SPY_DER_PYTHON:-/opt/spy-der/venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON=python3
rm -f /etc/spy-der/PRODUCTION_UNLOCKED
"$PYTHON" - <<'PY'
from pathlib import Path
import yaml
p=Path('/etc/spy-der/config.yaml')
d=yaml.safe_load(p.read_text())
d['trading']['enabled']=False
d['trading']['paper_mode']=True
d['trading']['submit_orders']=False
p.write_text(yaml.safe_dump(d, sort_keys=False))
PY
systemctl restart spy-der.target
echo "Production submission locked."

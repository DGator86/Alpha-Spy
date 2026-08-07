#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }
read -rsp "Tradier API token: " TOKEN; echo
read -rp "Tradier account ID: " ACCOUNT
read -rp "Environment [sandbox/production] (default sandbox): " ENVIRONMENT
ENVIRONMENT="${ENVIRONMENT:-sandbox}"
[[ "$ENVIRONMENT" == sandbox || "$ENVIRONMENT" == production ]] || { echo "Invalid environment"; exit 1; }
python3 - "$TOKEN" "$ACCOUNT" "$ENVIRONMENT" <<'PY'
from pathlib import Path
import shlex
import sys
p=Path('/etc/alpha-spy/secrets.env')
updates={'TRADIER_ACCESS_TOKEN':sys.argv[1],'TRADIER_ACCOUNT_ID':sys.argv[2],'TRADIER_ENVIRONMENT':sys.argv[3]}
lines=p.read_text().splitlines() if p.exists() else []
seen=set(); out=[]
for line in lines:
    key=line.split('=',1)[0] if '=' in line else ''
    if key in updates:
        out.append(f'{key}={shlex.quote(updates[key])}'); seen.add(key)
    else: out.append(line)
for key,val in updates.items():
    if key not in seen: out.append(f'{key}={shlex.quote(val)}')
p.write_text('\n'.join(out)+'\n')
PY
chmod 640 /etc/alpha-spy/secrets.env
chown root:alphaspy /etc/alpha-spy/secrets.env
systemctl restart alpha-spy.target
sleep 5
/opt/alpha-spy/venv/bin/alpha-spy --config /etc/alpha-spy/config.yaml doctor

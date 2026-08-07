#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }
python3 - <<'PY'
from pathlib import Path
import secrets, shlex
p=Path('/etc/alpha-spy/secrets.env')
lines=p.read_text().splitlines() if p.exists() else []
updates={
 'ALPHA_SPY_VIEW_TOKEN':secrets.token_urlsafe(32),
 'ALPHA_SPY_ADMIN_TOKEN':secrets.token_urlsafe(32),
 'ALPHA_SPY_INGEST_TOKEN':secrets.token_urlsafe(32),
}
out=[]; seen=set()
for line in lines:
 key=line.split('=',1)[0] if '=' in line else ''
 if key in updates:
  out.append(f'{key}={shlex.quote(updates[key])}'); seen.add(key)
 else: out.append(line)
for key,value in updates.items():
 if key not in seen: out.append(f'{key}={shlex.quote(value)}')
p.write_text('\n'.join(out)+'\n')
Path('/root/alpha-spy-new-dashboard-tokens.txt').write_text(
 '\n'.join(f'{k}={v}' for k,v in updates.items())+'\n'
)
PY
chmod 640 /etc/alpha-spy/secrets.env
chown root:alphaspy /etc/alpha-spy/secrets.env
chmod 600 /root/alpha-spy-new-dashboard-tokens.txt
systemctl restart alpha-spy-dashboard.service alpha-spy-engine.service
echo 'Tokens rotated. Read /root/alpha-spy-new-dashboard-tokens.txt'

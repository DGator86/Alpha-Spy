#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }
python3 - <<'PY'
from pathlib import Path
import secrets, shlex
p=Path('/etc/spy-der/secrets.env')
lines=p.read_text().splitlines() if p.exists() else []
updates={
 'SPY_DER_VIEW_TOKEN':secrets.token_urlsafe(32),
 'SPY_DER_ADMIN_TOKEN':secrets.token_urlsafe(32),
 'SPY_DER_INGEST_TOKEN':secrets.token_urlsafe(32),
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
Path('/root/spy-der-new-dashboard-tokens.txt').write_text(
 '\n'.join(f'{k}={v}' for k,v in updates.items())+'\n'
)
PY
chmod 640 /etc/spy-der/secrets.env
chown root:spyder /etc/spy-der/secrets.env
chmod 600 /root/spy-der-new-dashboard-tokens.txt
systemctl restart spy-der-dashboard.service spy-der-engine.service
echo 'Tokens rotated. Read /root/spy-der-new-dashboard-tokens.txt'

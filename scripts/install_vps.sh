#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash scripts/install_vps.sh" >&2; exit 1; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/var/backups/spy-der-pre-v2-$STAMP"
mkdir -p "$BACKUP_DIR"

echo "[1/9] Installing Ubuntu dependencies"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip sqlite3 zstd rclone curl ca-certificates

echo "[2/9] Stopping existing SPY services without deleting data"
systemctl stop spy-der.target 2>/dev/null || true
for unit in spy-der-market spy-der-engine spy-der-confirmation spy-der-settlement spy-der-decision spy-der-dashboard spy-der-dojo spy-der-backup; do
  systemctl stop "$unit.service" 2>/dev/null || true
done
for timer in spy-der-dojo.timer spy-der-backup.timer spy-drive-backup.timer; do
  systemctl stop "$timer" 2>/dev/null || true
  systemctl disable "$timer" 2>/dev/null || true
done
systemctl stop spy-drive-backup.service 2>/dev/null || true
systemctl disable spy-drive-backup.service 2>/dev/null || true
pkill -TERM -f '/opt/spy-der/venv/bin/spy-der' 2>/dev/null || true
sleep 2

if [[ -d /opt/spy-der ]]; then
  mv /opt/spy-der "/opt/spy-der.pre-v2-$STAMP"
fi
mkdir -p /opt/spy-der
cp -a "$ROOT_DIR"/. /opt/spy-der/release/

echo "[3/9] Creating restricted service account"
id spyder >/dev/null 2>&1 || useradd --system --home /var/lib/spy-der --shell /usr/sbin/nologin spyder
mkdir -p /etc/spy-der /var/lib/spy-der/{journal,dashboard,reference,models,reports,market,candidates,audit,positions,backups} /var/log/spy-der

if [[ -f /etc/spy-der/config.yaml ]]; then
  cp -a /etc/spy-der/config.yaml "$BACKUP_DIR/config.yaml"
fi
if [[ -f /etc/spy-der/secrets.env ]]; then
  cp -a /etc/spy-der/secrets.env "$BACKUP_DIR/secrets.env"
fi

echo "[4/9] Installing the local Python wheel"
python3 -m venv /opt/spy-der/venv
/opt/spy-der/venv/bin/pip install --upgrade pip wheel
WHEEL="$(find "$ROOT_DIR/dist" -maxdepth 1 -name 'spy_constituent_alpha_suite-*.whl' | head -n1)"
[[ -n "$WHEEL" ]] || { echo "Wheel missing from dist/. Rebuild the package." >&2; exit 1; }
/opt/spy-der/venv/bin/pip install "$WHEEL"

echo "[5/9] Installing fail-closed configuration"
cp "$ROOT_DIR/config/suite.yaml" /etc/spy-der/config.yaml
cp "$ROOT_DIR/config/universe.csv" /etc/spy-der/universe.csv
chmod 640 /etc/spy-der/config.yaml /etc/spy-der/universe.csv
chown root:spyder /etc/spy-der/config.yaml /etc/spy-der/universe.csv
rm -f /etc/spy-der/PRODUCTION_UNLOCKED

VIEW_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
INGEST_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
TRADIER_TOKEN=""
TRADIER_ACCOUNT=""
TRADIER_ENV="sandbox"

# Recover common values from a prior YAML configuration when present.
if [[ -f "$BACKUP_DIR/config.yaml" ]]; then
  mapfile -t recovered < <(/opt/spy-der/venv/bin/python - "$BACKUP_DIR/config.yaml" <<'PY'
import sys, yaml
p=sys.argv[1]
try: data=yaml.safe_load(open(p)) or {}
except Exception: data={}
def get(*paths):
    for path in paths:
        cur=data
        ok=True
        for part in path.split('.'):
            if isinstance(cur,dict) and part in cur: cur=cur[part]
            else: ok=False; break
        if ok and cur not in (None,''): return str(cur)
    return ''
print(get('tradier.access_token','tradier.token','broker.access_token','access_token'))
print(get('tradier.account_id','broker.account_id','account_id'))
print(get('tradier.environment','broker.environment') or 'sandbox')
PY
)
  TRADIER_TOKEN="${recovered[0]:-}"
  TRADIER_ACCOUNT="${recovered[1]:-}"
  [[ "${recovered[2]:-sandbox}" == "production" ]] && TRADIER_ENV=production || true
fi

# Prefer an existing trusted root-owned environment file when it contains values.
if [[ -f "$BACKUP_DIR/secrets.env" ]]; then
  unset TRADIER_ACCESS_TOKEN TRADIER_ACCOUNT_ID TRADIER_ENVIRONMENT \
        SPY_DER_VIEW_TOKEN SPY_DER_ADMIN_TOKEN SPY_DER_INGEST_TOKEN || true
  set +u
  set -a
  # shellcheck disable=SC1090
  source "$BACKUP_DIR/secrets.env"
  set +a
  set -u
  [[ -n "${TRADIER_ACCESS_TOKEN:-}" ]] && TRADIER_TOKEN="$TRADIER_ACCESS_TOKEN"
  [[ -n "${TRADIER_ACCOUNT_ID:-}" ]] && TRADIER_ACCOUNT="$TRADIER_ACCOUNT_ID"
  [[ -n "${TRADIER_ENVIRONMENT:-}" ]] && TRADIER_ENV="$TRADIER_ENVIRONMENT"
  [[ -n "${SPY_DER_VIEW_TOKEN:-}" ]] && VIEW_TOKEN="$SPY_DER_VIEW_TOKEN"
  [[ -n "${SPY_DER_ADMIN_TOKEN:-}" ]] && ADMIN_TOKEN="$SPY_DER_ADMIN_TOKEN"
  [[ -n "${SPY_DER_INGEST_TOKEN:-}" ]] && INGEST_TOKEN="$SPY_DER_INGEST_TOKEN"
fi

python3 - "$TRADIER_TOKEN" "$TRADIER_ACCOUNT" "$TRADIER_ENV" "$VIEW_TOKEN" "$ADMIN_TOKEN" "$INGEST_TOKEN" <<'PY'
from pathlib import Path
import shlex
import sys
keys = (
    "TRADIER_ACCESS_TOKEN",
    "TRADIER_ACCOUNT_ID",
    "TRADIER_ENVIRONMENT",
    "SPY_DER_VIEW_TOKEN",
    "SPY_DER_ADMIN_TOKEN",
    "SPY_DER_INGEST_TOKEN",
)
Path("/etc/spy-der/secrets.env").write_text(
    "".join(f"{key}={shlex.quote(value)}\n" for key, value in zip(keys, sys.argv[1:])),
    encoding="utf-8",
)
PY
chmod 640 /etc/spy-der/secrets.env
chown root:spyder /etc/spy-der/secrets.env

python3 - "gdrive:SPY Trading Backups/$(hostname -s)" <<'PY'
from pathlib import Path
import shlex
import sys
Path("/etc/spy-der/backup.env").write_text(
    "RCLONE_CONFIG=/root/.config/rclone/rclone.conf\n"
    f"SPY_DER_BACKUP_REMOTE={shlex.quote(sys.argv[1])}\n",
    encoding="utf-8",
)
PY
chmod 600 /etc/spy-der/backup.env

chown -R spyder:spyder /var/lib/spy-der /var/log/spy-der

echo "[6/9] Installing services and backup tooling"
cp "$ROOT_DIR"/systemd/* /etc/systemd/system/
cp "$ROOT_DIR/scripts/spy-der-drive-backup" /usr/local/sbin/spy-der-drive-backup
chmod 700 /usr/local/sbin/spy-der-drive-backup
systemctl daemon-reload
systemctl enable spy-der.target spy-der-dojo.timer

if rclone about gdrive: >/dev/null 2>&1; then
  systemctl enable spy-der-backup.timer
  BACKUP_STATUS=enabled
else
  BACKUP_STATUS="not enabled (gdrive remote not authorized for root)"
fi

echo "[7/9] Validating database and configuration"
set -a; source /etc/spy-der/secrets.env; set +a
/opt/spy-der/venv/bin/spy-der --config /etc/spy-der/config.yaml doctor

echo "[8/9] Starting the complete suite"
systemctl enable --now spy-der.target
systemctl enable --now spy-der-dojo.timer
[[ "$BACKUP_STATUS" == enabled ]] && systemctl enable --now spy-der-backup.timer || true
sleep 8
curl -fsS http://127.0.0.1:8787/health >/dev/null
curl -fsS http://127.0.0.1:8788/api/v1/health >/dev/null

cat >/root/spy-der-credentials.txt <<EOF
SPY Constituent Alpha Suite v2.0.0
Dashboard URL through SSH tunnel: http://127.0.0.1:8788
Decision API through SSH tunnel: http://127.0.0.1:8787
View token: $VIEW_TOKEN
Admin token: $ADMIN_TOKEN
Ingest token: $INGEST_TOKEN
Backup timer: $BACKUP_STATUS
Prior configuration backup: $BACKUP_DIR
Production trading: LOCKED
EOF
chmod 600 /root/spy-der-credentials.txt

echo "[9/9] Installation complete"
echo
cat /root/spy-der-credentials.txt
echo
echo "From Windows PowerShell:"
echo "ssh -L 8788:127.0.0.1:8788 -L 8787:127.0.0.1:8787 root@$(hostname -I | awk '{print $1}')"

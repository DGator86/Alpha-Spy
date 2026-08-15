#!/usr/bin/env bash
# Install Alpha-SPY on a fresh Ubuntu 24.04 host.
#
# Alpha-SPY is a standalone product. This installer assumes a fresh
# installation: it carries no migration path, recovers no credentials from a
# previous configuration, preserves no previous application directory, and
# touches only Alpha-SPY-owned paths, units and the alphaspy service account.
# Any other application on the host is left alone.
set -Eeuo pipefail
umask 077

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash scripts/install_vps.sh" >&2; exit 1; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ALPHA_SPY_UNITS=(
  alpha-spy-market
  alpha-spy-engine
  alpha-spy-confirmation
  alpha-spy-settlement
  alpha-spy-decision
  alpha-spy-dashboard
  alpha-spy-dojo
  alpha-spy-validation
  alpha-spy-event-calendar
  alpha-spy-backup
)

echo "[1/9] Installing Ubuntu dependencies"
apt-get update
# python3-yaml backs the operator scripts that edit /etc/alpha-spy/config.yaml.
# They prefer /opt/alpha-spy/venv/bin/python, which always has PyYAML, but keep
# the system interpreter usable as a fallback on a minimal image.
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip python3-yaml sqlite3 zstd rclone curl ca-certificates

echo "[2/9] Stopping any running Alpha-SPY services"
systemctl stop alpha-spy.target 2>/dev/null || true
for unit in "${ALPHA_SPY_UNITS[@]}"; do
  systemctl stop "$unit.service" 2>/dev/null || true
done
for timer in alpha-spy-dojo.timer alpha-spy-validation.timer alpha-spy-event-calendar.timer alpha-spy-backup.timer; do
  systemctl stop "$timer" 2>/dev/null || true
done
pkill -TERM -f '/opt/alpha-spy/venv/bin/alpha-spy' 2>/dev/null || true
sleep 2

echo "[3/9] Installing the application tree"
rm -rf /opt/alpha-spy/release
mkdir -p /opt/alpha-spy/release
cp -a "$ROOT_DIR"/. /opt/alpha-spy/release/

echo "[4/9] Creating the restricted service account and data roots"
id alphaspy >/dev/null 2>&1 || \
  useradd --system --home /var/lib/alpha-spy --shell /usr/sbin/nologin alphaspy
mkdir -p /etc/alpha-spy \
  /var/lib/alpha-spy/{journal,dashboard,reference,models,reports,market,candidates,audit,replay,validation,positions,backups} \
  /var/lib/alpha-spy/models/challengers \
  /var/lib/alpha-spy/reports/dojo \
  /var/lib/alpha-spy/dojo \
  /var/log/alpha-spy

# The installer runs under umask 077, so directories it creates default to
# 700 root:root. The alphaspy service account must be able to traverse
# /etc/alpha-spy to read the 640 root:alphaspy config and secrets inside it.
chown root:alphaspy /etc/alpha-spy
chmod 750 /etc/alpha-spy

echo "[5/9] Installing the application wheel"
rm -rf /opt/alpha-spy/venv
python3 -m venv /opt/alpha-spy/venv
/opt/alpha-spy/venv/bin/pip install --upgrade pip wheel
WHEEL="$(find "$ROOT_DIR/dist" -maxdepth 1 -name 'alpha_spy-*.whl' | head -n1)"
[[ -n "$WHEEL" ]] || { echo "Wheel missing from dist/. Rebuild the package." >&2; exit 1; }
/opt/alpha-spy/venv/bin/pip install "$WHEEL"

# umask 077 also makes the venv (and /opt/alpha-spy itself) unreadable by the
# alphaspy service account, which systemd then reports as "Failed to execute
# /opt/alpha-spy/venv/bin/alpha-spy: Permission denied". Nothing under the
# application tree is secret, so restore world read/traverse without touching
# write bits.
chmod a+rX /opt/alpha-spy
chmod -R a+rX /opt/alpha-spy/venv /opt/alpha-spy/release

echo "[6/9] Installing fail-closed configuration"
cp "$ROOT_DIR/config/suite.yaml" /etc/alpha-spy/config.yaml
cp "$ROOT_DIR/config/universe.csv" /etc/alpha-spy/universe.csv
chmod 640 /etc/alpha-spy/config.yaml /etc/alpha-spy/universe.csv
chown root:alphaspy /etc/alpha-spy/config.yaml /etc/alpha-spy/universe.csv
rm -f /etc/alpha-spy/PRODUCTION_UNLOCKED

# Fresh credentials on every install. Nothing is read from a previous
# configuration; Tradier is configured afterwards with configure_tradier.sh.
VIEW_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
INGEST_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

python3 - "$VIEW_TOKEN" "$ADMIN_TOKEN" "$INGEST_TOKEN" <<'PY'
from pathlib import Path
import shlex
import sys

values = {
    "TRADIER_MARKET_ACCESS_TOKEN": "",
    "TRADIER_STREAM_ENABLED": "true",
    "TRADIER_EXECUTION_ACCESS_TOKEN": "",
    "TRADIER_EXECUTION_ACCOUNT_ID": "",
    "TRADIER_EXECUTION_ENVIRONMENT": "sandbox",
    "TRADIER_PRODUCTION_EXECUTION_ACCESS_TOKEN": "",
    "TRADIER_PRODUCTION_EXECUTION_ACCOUNT_ID": "",
    "ALPHA_SPY_TRADING_ENABLED": "false",
    "ALPHA_SPY_SUBMIT_ORDERS": "false",
    "ALPHA_SPY_PAPER_MODE": "true",
    "ALPHA_SPY_VIEW_TOKEN": sys.argv[1],
    "ALPHA_SPY_ADMIN_TOKEN": sys.argv[2],
    "ALPHA_SPY_INGEST_TOKEN": sys.argv[3],
}
Path("/etc/alpha-spy/secrets.env").write_text(
    "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()),
    encoding="utf-8",
)
PY
chmod 640 /etc/alpha-spy/secrets.env
chown root:alphaspy /etc/alpha-spy/secrets.env

python3 - "gdrive:Alpha-SPY Backups/$(hostname -s)" <<'PY'
from pathlib import Path
import shlex
import sys
Path("/etc/alpha-spy/backup.env").write_text(
    "RCLONE_CONFIG=/root/.config/rclone/rclone.conf\n"
    f"ALPHA_SPY_BACKUP_REMOTE={shlex.quote(sys.argv[1])}\n",
    encoding="utf-8",
)
PY
chmod 600 /etc/alpha-spy/backup.env

chown -R alphaspy:alphaspy /var/lib/alpha-spy /var/log/alpha-spy

echo "[7/9] Installing services and backup tooling"
cp "$ROOT_DIR"/systemd/* /etc/systemd/system/
cp "$ROOT_DIR/scripts/alpha-spy-backup" /usr/local/sbin/alpha-spy-backup
chmod 700 /usr/local/sbin/alpha-spy-backup
systemctl daemon-reload
systemctl enable alpha-spy.target alpha-spy-dojo.timer alpha-spy-validation.timer

if rclone about gdrive: >/dev/null 2>&1; then
  systemctl enable alpha-spy-backup.timer
  BACKUP_STATUS=enabled
else
  BACKUP_STATUS="not enabled (gdrive remote not authorized for root)"
fi

echo "[8/9] Validating configuration and database"
set -a; source /etc/alpha-spy/secrets.env; set +a
/opt/alpha-spy/venv/bin/alpha-spy --config /etc/alpha-spy/config.yaml doctor

# doctor runs as root and initializes the journal/dashboard databases, leaving
# root-owned files under /var/lib/alpha-spy that the alphaspy services cannot
# open ("sqlite3.OperationalError: unable to open database file"). Re-assert
# service-account ownership after the root validation pass.
chown -R alphaspy:alphaspy /var/lib/alpha-spy /var/log/alpha-spy

echo "[9/9] Starting Alpha-SPY"
systemctl enable --now alpha-spy.target
systemctl enable --now alpha-spy-dojo.timer alpha-spy-validation.timer
[[ "$BACKUP_STATUS" == enabled ]] && systemctl enable --now alpha-spy-backup.timer || true
sleep 8
curl -fsS http://127.0.0.1:8787/health >/dev/null
curl -fsS http://127.0.0.1:8788/api/v1/health >/dev/null

cat >/root/alpha-spy-credentials.txt <<EOF
Alpha-SPY
Dashboard URL through SSH tunnel: http://127.0.0.1:8788
Decision API through SSH tunnel: http://127.0.0.1:8787
View token: $VIEW_TOKEN
Admin token: $ADMIN_TOKEN
Ingest token: $INGEST_TOKEN
Backup timer: $BACKUP_STATUS
Production trading: LOCKED
EOF
chmod 600 /root/alpha-spy-credentials.txt

echo
echo "Installation complete"
echo
cat /root/alpha-spy-credentials.txt
echo
echo "Open the Command Center through an SSH tunnel:"
echo "ssh -L 8788:127.0.0.1:8788 -L 8787:127.0.0.1:8787 root@$(hostname -I | awk '{print $1}')"

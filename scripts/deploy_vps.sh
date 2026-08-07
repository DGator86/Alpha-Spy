#!/usr/bin/env bash
# Deploy a release to the single VPS over SSH.
#
# Builds (or reuses) a release archive, verifies it locally, transfers it,
# verifies it again on the target, then runs the suite's own installer. The
# installer performs a fresh installation and touches only Alpha-SPY paths.
# /opt/alpha-spy tree, and brings the suite up in the shipped fail-closed
# posture: Tradier sandbox, paper mode, order submission disabled, no
# production sentinel.
#
# This script never enables production trading. That still requires the
# separate, deliberate steps in docs/OPERATIONS.md.
#
# Required:
#   DEPLOY_HOST          target hostname or IP
#
# Optional:
#   DEPLOY_USER          SSH user (default: root)
#   DEPLOY_PORT          SSH port (default: 22)
#   DEPLOY_SSH_KEY       private key path (default: ssh agent / ~/.ssh config)
#   DEPLOY_REMOTE_DIR    staging directory on the target (default: /root)
#   DEPLOY_ARCHIVE       archive to deploy (default: build a fresh one)
#   DEPLOY_SKIP_BUILD    1 to reuse the newest archive in dist/release
#   DEPLOY_ASSUME_YES    1 to skip the interactive confirmation
#
# Usage: DEPLOY_HOST=1.2.3.4 scripts/deploy_vps.sh
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEPLOY_HOST="${DEPLOY_HOST:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_REMOTE_DIR="${DEPLOY_REMOTE_DIR:-/root}"
DEPLOY_ARCHIVE="${DEPLOY_ARCHIVE:-}"

[[ -n "$DEPLOY_HOST" ]] || {
  echo "DEPLOY_HOST is required. Example: DEPLOY_HOST=1.2.3.4 scripts/deploy_vps.sh" >&2
  exit 1
}

SSH_OPTS=(-p "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=15)
SCP_OPTS=(-P "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=15)
if [[ -n "${DEPLOY_SSH_KEY:-}" ]]; then
  SSH_OPTS+=(-i "$DEPLOY_SSH_KEY" -o IdentitiesOnly=yes)
  SCP_OPTS+=(-i "$DEPLOY_SSH_KEY" -o IdentitiesOnly=yes)
fi
TARGET="$DEPLOY_USER@$DEPLOY_HOST"

remote() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

echo "==> [1/6] Preparing the release archive"
if [[ -z "$DEPLOY_ARCHIVE" ]]; then
  if [[ "${DEPLOY_SKIP_BUILD:-0}" != "1" ]]; then
    bash scripts/build_release.sh
  fi
  DEPLOY_ARCHIVE="$(find "$ROOT_DIR/dist/release" -maxdepth 1 -name 'alpha-spy-v*.tar.gz' 2>/dev/null | sort | tail -n1)"
fi
[[ -n "$DEPLOY_ARCHIVE" && -f "$DEPLOY_ARCHIVE" ]] || {
  echo "No release archive available. Run 'make release' or set DEPLOY_ARCHIVE." >&2
  exit 1
}
ARCHIVE_NAME="$(basename "$DEPLOY_ARCHIVE")"
TREE_NAME="${ARCHIVE_NAME%.tar.gz}"

echo "==> [2/6] Verifying the archive locally"
bash scripts/verify_release.sh "$DEPLOY_ARCHIVE"

echo
echo "  archive: $ARCHIVE_NAME"
echo "  target:  $TARGET:$DEPLOY_PORT"
echo "  staging: $DEPLOY_REMOTE_DIR/$TREE_NAME"
echo
echo "  This stops the running suite, replaces /opt/alpha-spy, and restarts all"
echo "  services in sandbox/paper mode. Data under /var/lib/alpha-spy and"
echo "  Production trading stays locked."
echo
if [[ "${DEPLOY_ASSUME_YES:-0}" != "1" ]]; then
  read -r -p "Type DEPLOY to continue: " reply
  [[ "$reply" == "DEPLOY" ]] || { echo "Aborted."; exit 1; }
fi

echo
echo "==> [3/6] Checking connectivity and target state"
remote "set -Eeuo pipefail
  . /etc/os-release
  echo \"    host: \$(hostname -s)  (\$NAME \$VERSION_ID)\"
  command -v sudo >/dev/null || { echo 'sudo is not installed on the target' >&2; exit 1; }
  sudo -n true || { echo 'Passwordless sudo is required on the target' >&2; exit 1; }
  df -h --output=avail /var | tail -n1 | xargs echo '    /var available:'"

echo
echo "==> [4/6] Transferring the release"
scp "${SCP_OPTS[@]}" "$DEPLOY_ARCHIVE" "$DEPLOY_ARCHIVE.sha256" "$TARGET:$DEPLOY_REMOTE_DIR/"
remote "set -Eeuo pipefail
  cd '$DEPLOY_REMOTE_DIR'
  sha256sum -c '$ARCHIVE_NAME.sha256'
  rm -rf '$TREE_NAME'
  tar -xzf '$ARCHIVE_NAME'
  bash '$TREE_NAME/scripts/verify_release.sh' '$ARCHIVE_NAME'"

echo
echo "==> [5/6] Running the installer"
remote "set -Eeuo pipefail
  cd '$DEPLOY_REMOTE_DIR/$TREE_NAME'
  sudo bash install.sh"

echo
echo "==> [6/6] Post-install status"
remote "sudo bash '$DEPLOY_REMOTE_DIR/$TREE_NAME/scripts/status.sh'" || true

echo
echo "Deployment complete."
echo "Credentials on the target: sudo cat /root/alpha-spy-credentials.txt"
echo "Open the GUI through an SSH tunnel:"
echo "  ssh -L 8788:127.0.0.1:8788 -L 8787:127.0.0.1:8787 -p $DEPLOY_PORT $TARGET"
echo "  then browse to http://127.0.0.1:8788"

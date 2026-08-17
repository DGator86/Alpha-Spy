#!/usr/bin/env bash
# Verify the systemd units with systemd-analyze on a machine where the suite
# is not installed.
#
# systemd-analyze reports every ExecStart binary that is missing, which on a
# build machine is all of them — the units point at install-time paths under
# /opt/alpha-spy/venv/bin and /usr/local/sbin. Those specific complaints are
# expected and filtered. Anything else is a real defect and fails the run.
#
# To keep the filter from hiding a typo, every ExecStart in the units is first
# checked against the paths the installer actually creates. A path that drifts
# is rejected here, and its missing-binary complaint is then no longer filtered.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Paths scripts/install_vps.sh puts in place.
INSTALLED_BINARIES=(
  /opt/alpha-spy/venv/bin/alpha-spy
  /usr/local/sbin/alpha-spy-backup
  /usr/local/sbin/alpha-spy-prune
  /usr/local/sbin/alpha-spy-ordinary-calendar
)

echo "==> Checking ExecStart paths against what the installer creates"
STATUS=0
while IFS= read -r line; do
  binary="${line#*=}"
  binary="${binary%% *}"
  binary="${binary#[-@+!:]}"
  known=0
  for candidate in "${INSTALLED_BINARIES[@]}"; do
    [[ "$binary" == "$candidate" ]] && known=1 && break
  done
  if [[ "$known" -eq 0 ]]; then
    echo "    unexpected ExecStart binary: $binary" >&2
    STATUS=1
  fi
done < <(grep -h '^ExecStart=' systemd/*.service | sort -u)
[[ "$STATUS" -eq 0 ]] || { echo "ExecStart paths do not match the installer" >&2; exit 1; }
for candidate in "${INSTALLED_BINARIES[@]}"; do
  echo "    ok: $candidate"
done

if ! command -v systemd-analyze >/dev/null 2>&1; then
  echo "==> systemd-analyze is not available; skipping unit verification"
  exit 0
fi

echo "==> systemd-analyze verify"
OUTPUT="$(systemd-analyze verify systemd/*.service systemd/*.timer systemd/*.target 2>&1 || true)"

FILTER=""
for candidate in "${INSTALLED_BINARIES[@]}"; do
  FILTER+="${FILTER:+|}Command ${candidate//\//\\/} is not executable: No such file or directory"
done
REMAINING="$(printf '%s\n' "$OUTPUT" | grep -vE "$FILTER" | grep -v '^[[:space:]]*$' || true)"

if [[ -n "$REMAINING" ]]; then
  echo "systemd-analyze reported problems beyond the expected missing binaries:" >&2
  printf '%s\n' "$REMAINING" >&2
  exit 1
fi

UNITS="$(find systemd -maxdepth 1 -type f \( -name '*.service' -o -name '*.timer' -o -name '*.target' \) | wc -l)"
echo "    $UNITS units verified; only the expected missing-binary notices were reported"

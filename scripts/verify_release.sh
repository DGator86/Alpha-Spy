#!/usr/bin/env bash
# Verify a release archive without installing it.
#
# Extracts the archive to a temporary directory and checks every file against
# the RELEASE_MANIFEST.sha256 it carries, then confirms the pieces the
# installer depends on are present. Run this on the VPS after transferring a
# release, before running install.sh.
#
# Usage: scripts/verify_release.sh <archive.tar.gz|archive.zip>
set -Eeuo pipefail

ARCHIVE="${1:-}"
[[ -n "$ARCHIVE" && -f "$ARCHIVE" ]] || {
  echo "Usage: scripts/verify_release.sh <archive.tar.gz|archive.zip>" >&2
  exit 1
}
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> Verifying $(basename "$ARCHIVE")"

# Check the sidecar checksum first when one sits next to the archive.
if [[ -f "$ARCHIVE.sha256" ]]; then
  (cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256")
else
  echo "    no .sha256 sidecar next to the archive; skipping that check"
fi

case "$ARCHIVE" in
  *.tar.gz|*.tgz) tar -xzf "$ARCHIVE" -C "$WORK" ;;
  *.zip)          unzip -q "$ARCHIVE" -d "$WORK" ;;
  *)              echo "Unsupported archive type: $ARCHIVE" >&2; exit 1 ;;
esac

TREE="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[[ -n "$TREE" ]] || { echo "Archive contains no top-level directory" >&2; exit 1; }
cd "$TREE"

[[ -f RELEASE_MANIFEST.sha256 ]] || { echo "RELEASE_MANIFEST.sha256 missing from the archive" >&2; exit 1; }

echo "==> Checking $(wc -l < RELEASE_MANIFEST.sha256) files against RELEASE_MANIFEST.sha256"
sha256sum -c --quiet RELEASE_MANIFEST.sha256

# The manifest proves the listed files are intact; this catches extra files
# that were added to the archive but never hashed.
EXPECTED="$(wc -l < RELEASE_MANIFEST.sha256)"
ACTUAL="$(find . -type f ! -name RELEASE_MANIFEST.sha256 | wc -l)"
if [[ "$EXPECTED" != "$ACTUAL" ]]; then
  echo "Manifest covers $EXPECTED files but the archive holds $ACTUAL" >&2
  exit 1
fi

echo "==> Checking installer prerequisites"
for path in install.sh scripts/install_vps.sh config/suite.yaml config/universe.csv; do
  [[ -f "$path" ]] || { echo "Missing required file: $path" >&2; exit 1; }
  echo "    present: $path"
done
WHEEL="$(find dist -maxdepth 1 -name 'alpha_spy-*.whl' | head -n1)"
[[ -n "$WHEEL" ]] || { echo "Missing application wheel under dist/" >&2; exit 1; }
echo "    present: $WHEEL"

UNITS="$(find systemd -name '*.service' -o -name '*.timer' -o -name '*.target' | wc -l)"
[[ "$UNITS" -ge 11 ]] || { echo "Expected at least 11 systemd units, found $UNITS" >&2; exit 1; }
echo "    present: $UNITS systemd units"

echo
echo "Release verified: $(basename "$ARCHIVE")"

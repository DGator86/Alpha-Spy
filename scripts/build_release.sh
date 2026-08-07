#!/usr/bin/env bash
# Build a distributable SPY Constituent Alpha Suite release.
#
# Produces, under release/<version>/:
#   spy-constituent-alpha-suite-v<version>.tar.gz  (+ .sha256)
#   spy-constituent-alpha-suite-v<version>.zip     (+ .sha256)
#   RELEASE_MANIFEST.sha256
#
# The archives contain the same layout the v2.0.0 release shipped: source
# tree, tests, examples, docs, config, systemd units, operator scripts, the
# GUI preview, the bundled wheel under dist/, and a manifest of every file.
# scripts/install_vps.sh installs straight out of an extracted archive.
#
# Archives are built reproducibly: file ordering is sorted, ownership is
# normalised, and every timestamp is pinned to SOURCE_DATE_EPOCH (defaulting
# to the HEAD commit time), so the same tree always yields the same checksum.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-python3}"

VERSION="$(
  "$PYTHON" - <<'PY'
import pathlib, re, sys
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
if not match:
    sys.exit("Could not read version from pyproject.toml")
print(match.group(1))
PY
)"

NAME="spy-constituent-alpha-suite-v$VERSION"
STAGE_ROOT="$ROOT_DIR/dist/.stage"
STAGE="$STAGE_ROOT/$NAME"
# Build output only. The committed release/ directory holds the archived
# upstream drops and is never written to by this script.
OUT_DIR="${RELEASE_OUT_DIR:-$ROOT_DIR/dist/release}"

if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
  SOURCE_DATE_EPOCH="$(git -C "$ROOT_DIR" log -1 --pretty=%ct 2>/dev/null || date -u +%s)"
fi
export SOURCE_DATE_EPOCH
BUILD_DATE="$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ)"

echo "==> Building $NAME (SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH, $BUILD_DATE)"

echo "==> [1/6] Cleaning previous build output"
rm -rf build dist ./*.egg-info src/*.egg-info
mkdir -p "$STAGE" "$OUT_DIR"

echo "==> [2/6] Building the application wheel"
"$PYTHON" -m build --wheel --outdir "$ROOT_DIR/dist"
WHEEL="$(find "$ROOT_DIR/dist" -maxdepth 1 -name 'spy_constituent_alpha_suite-*.whl' | head -n1)"
[[ -n "$WHEEL" ]] || { echo "Wheel build produced no artifact" >&2; exit 1; }
echo "    wheel: $(basename "$WHEEL")"

echo "==> [3/6] Staging the release tree"
# Explicit include list. Repository-only material (.github, .gitignore,
# release/, working notes) is deliberately kept out of the archive.
RELEASE_PATHS=(
  BUILD_REPORT.md
  CHANGELOG.md
  Makefile
  README.md
  config
  docs
  examples
  install.sh
  preview
  pyproject.toml
  scripts
  src
  systemd
  tests
)
for path in "${RELEASE_PATHS[@]}"; do
  [[ -e "$path" ]] || { echo "Missing release path: $path" >&2; exit 1; }
  cp -a "$path" "$STAGE/"
done
mkdir -p "$STAGE/dist"
cp -a "$WHEEL" "$STAGE/dist/"

# Never ship caches, compiled bytecode, or build metadata.
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE" -name '*.py[cod]' -delete
find "$STAGE" -name '*.egg-info' -type d -prune -exec rm -rf {} +
rm -rf "$STAGE/build" "$STAGE/.pytest_cache" "$STAGE/.ruff_cache"

chmod 0755 "$STAGE/install.sh" "$STAGE"/scripts/*.sh "$STAGE/scripts/spy-der-drive-backup"

echo "==> [4/6] Writing RELEASE_MANIFEST.sha256"
# The manifest covers every file except itself; it cannot hash its own contents.
(
  cd "$STAGE"
  find . -type f ! -name RELEASE_MANIFEST.sha256 -print0 \
    | sort -z | xargs -0 sha256sum > RELEASE_MANIFEST.sha256
)
echo "    $(wc -l < "$STAGE/RELEASE_MANIFEST.sha256") files hashed"

# Pin every timestamp so the archives are byte-reproducible.
find "$STAGE" -exec touch --no-dereference --date="@$SOURCE_DATE_EPOCH" {} +

echo "==> [5/6] Creating archives"
rm -f "$OUT_DIR/$NAME.tar.gz" "$OUT_DIR/$NAME.zip"
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@$SOURCE_DATE_EPOCH" \
    -C "$STAGE_ROOT" -cf - "$NAME" \
  | gzip -9n > "$OUT_DIR/$NAME.tar.gz"

# -X drops extra file attributes; the sorted file list keeps entry order stable.
(
  cd "$STAGE_ROOT"
  find "$NAME" -print | sort | zip -q -X -9 "$OUT_DIR/$NAME.zip" -@
)

echo "==> [6/6] Writing checksums"
cp -a "$STAGE/RELEASE_MANIFEST.sha256" "$OUT_DIR/RELEASE_MANIFEST.sha256"
(
  cd "$OUT_DIR"
  sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256"
  sha256sum "$NAME.zip" > "$NAME.zip.sha256"
)

rm -rf "$STAGE_ROOT"

echo
echo "Release built: $OUT_DIR"
cat "$OUT_DIR/$NAME.tar.gz.sha256" "$OUT_DIR/$NAME.zip.sha256"

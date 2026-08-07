#!/usr/bin/env bash
# Fail if any legacy identifier has re-entered the repository.
#
# Alpha-SPY is a standalone product with no migration layer. These identifiers
# belong to previous systems and must not reappear -- including by accident,
# such as a path that happens to contain one as a substring.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PATTERN='spy_der|spy-der|spy_alpha_dashboard|spy_constituent_alpha|zerodte|pre-v2|spy-drive-backup|suite-v2|command-center-v2'

# Build output and tool caches are not source; .git holds immutable history.
# This script is excluded because it necessarily spells out the pattern.
EXCLUDES=(
  --exclude="$(basename "${BASH_SOURCE[0]}")"
  --exclude-dir=.git
  --exclude-dir=dist
  --exclude-dir=build
  --exclude-dir=__pycache__
  --exclude-dir=.pytest_cache
  --exclude-dir=.ruff_cache
  --exclude-dir=.venv
  --exclude-dir=venv
  --exclude-dir=node_modules
  --exclude-dir=reports
)

echo "==> Checking for legacy identifiers"
echo "    pattern: $PATTERN"

if matches="$(grep -RniE "$PATTERN" . "${EXCLUDES[@]}" 2>/dev/null)"; then
  count="$(printf '%s\n' "$matches" | wc -l)"
  echo
  echo "Found $count legacy identifier(s). Alpha-SPY carries no legacy naming:" >&2
  printf '%s\n' "$matches" | sed 's/^/  /' >&2
  echo >&2
  echo "Rename to the Alpha-SPY namespace:" >&2
  echo "  package alpha_spy | CLI alpha-spy | /opt|/etc|/var/lib|/var/log/alpha-spy" >&2
  echo "  units alpha-spy-*.service|.timer|.target | account alphaspy" >&2
  echo "  databases alpha-spy.db and command-center.sqlite" >&2
  exit 1
fi

echo "    clean: no legacy identifiers present"

#!/usr/bin/env bash
# Exercise a built wheel end to end, independently of the source tree.
#
# Installs the wheel into a throwaway virtualenv, points the suite at a
# temporary state root, and verifies the same behaviour BUILD_REPORT.md
# records for the v2.0.0 release: configuration load, database integrity,
# a demo market cycle, a prediction/decision cycle, both API servers, and
# the dashboard's view/admin token separation.
#
# Fully hermetic: no Tradier credentials, no network. The market collector
# runs in demo mode (market.demo_when_unconfigured) and the universe is read
# from the bundled config/universe.csv.
#
# Usage: scripts/smoke_test.sh [path/to/wheel]
#        Defaults to the newest wheel in dist/.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-python3}"
WHEEL="${1:-}"
if [[ -z "$WHEEL" ]]; then
  WHEEL="$(find "$ROOT_DIR/dist" -maxdepth 1 -name 'spy_constituent_alpha_suite-*.whl' 2>/dev/null | sort | tail -n1)"
fi
[[ -n "$WHEEL" && -f "$WHEEL" ]] || {
  echo "No wheel found. Run 'make build' first, or pass a wheel path." >&2
  exit 1
}

WORK="$(mktemp -d)"
DASHBOARD_PID=""
DECISION_PID=""
FAILED=0

cleanup() {
  [[ -n "$DASHBOARD_PID" ]] && kill "$DASHBOARD_PID" 2>/dev/null || true
  [[ -n "$DECISION_PID" ]] && kill "$DECISION_PID" 2>/dev/null || true
  wait "$DASHBOARD_PID" "$DECISION_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

check() {
  local label="$1"
  shift
  if "$@" >"$WORK/last.out" 2>&1; then
    printf '  %-46s PASS\n' "$label"
  else
    printf '  %-46s FAIL\n' "$label"
    sed 's/^/      /' "$WORK/last.out" | tail -n 20
    FAILED=1
  fi
}

expect_status() {
  local label="$1" expected="$2" url="$3"
  shift 3
  local actual
  actual="$(curl -s -o /dev/null -w '%{http_code}' "$@" "$url" || echo 000)"
  if [[ "$actual" == "$expected" ]]; then
    printf '  %-46s PASS (%s)\n' "$label" "$actual"
  else
    printf '  %-46s FAIL (want %s, got %s)\n' "$label" "$expected" "$actual"
    FAILED=1
  fi
}

wait_for_http() {
  local url="$1" tries="${2:-40}"
  for _ in $(seq 1 "$tries"); do
    curl -fsS -o /dev/null "$url" 2>/dev/null && return 0
    sleep 0.5
  done
  return 1
}

echo "==> Smoke testing $(basename "$WHEEL")"
echo "    workspace: $WORK"

echo
echo "==> [1/6] Installing the wheel into an isolated virtualenv"
"$PYTHON" -m venv "$WORK/venv"
VENV_PY="$WORK/venv/bin/python"
"$VENV_PY" -m pip install --quiet --upgrade pip wheel
"$VENV_PY" -m pip install --quiet "$WHEEL"
SPY_DER="$WORK/venv/bin/spy-der"
[[ -x "$SPY_DER" ]] || { echo "spy-der console script missing from the wheel" >&2; exit 1; }

echo
echo "==> [2/6] Writing a hermetic configuration"
STATE_ROOT="$WORK/state"
CONFIG="$WORK/config.yaml"
VIEW_TOKEN="smoke-view-$RANDOM"
ADMIN_TOKEN="smoke-admin-$RANDOM"
INGEST_TOKEN="smoke-ingest-$RANDOM"
DASHBOARD_PORT="${SMOKE_DASHBOARD_PORT:-18788}"
DECISION_PORT="${SMOKE_DECISION_PORT:-18787}"

"$VENV_PY" - "$ROOT_DIR/config/suite.yaml" "$CONFIG" "$STATE_ROOT" "$ROOT_DIR/config/universe.csv" \
            "$VIEW_TOKEN" "$ADMIN_TOKEN" "$INGEST_TOKEN" "$DASHBOARD_PORT" <<'PY'
import sys, yaml
from pathlib import Path

src, dst, state_root, universe_csv, view, admin, ingest, port = sys.argv[1:9]
cfg = yaml.safe_load(Path(src).read_text(encoding="utf-8"))

cfg["paths"] = {
    "state_root": state_root,
    "database": f"{state_root}/journal/suite-v2.db",
    "dashboard_database": f"{state_root}/dashboard/command-center-v2.sqlite",
    "universe_cache": f"{state_root}/reference/universe.csv",
    "model_dir": f"{state_root}/models",
    "report_dir": f"{state_root}/reports",
    "log_dir": f"{state_root}/logs",
    "production_sentinel": f"{state_root}/PRODUCTION_UNLOCKED",
}
# Read the universe from disk so the test never reaches the iShares endpoint.
cfg["universe"]["source"] = "local_csv"
cfg["universe"]["local_csv"] = universe_csv
cfg["dashboard"].update(
    host="127.0.0.1",
    port=int(port),
    view_token=view,
    admin_token=admin,
    ingest_token=ingest,
    require_view_token=True,
)
# Keep the fail-closed posture the release ships with.
cfg["trading"].update(enabled=False, paper_mode=True, submit_orders=False)

Path(dst).write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
PY
echo "    config: $CONFIG"
echo "    state root: $STATE_ROOT"

RUN=("$SPY_DER" --config "$CONFIG" --state-root "$STATE_ROOT")

echo
echo "==> [3/6] Configuration, database and universe"
check "spy-der doctor (config + PRAGMA quick_check)" "${RUN[@]}" doctor
check "universe loads from local CSV" "${RUN[@]}" universe-refresh --force

echo
echo "==> [4/6] Demo market and decision cycle"
check "market cycle (demo mode)" "${RUN[@]}" run-once market
check "engine cycle (features/prediction/decision)" "${RUN[@]}" run-once engine
check "confirmation cycle" "${RUN[@]}" run-once confirmation
check "dashboard state builds" "${RUN[@]}" state

echo
echo "==> [5/6] Local API servers"
"${RUN[@]}" dashboard-api --host 127.0.0.1 --port "$DASHBOARD_PORT" >"$WORK/dashboard.log" 2>&1 &
DASHBOARD_PID=$!
"${RUN[@]}" decision-service --host 127.0.0.1 --port "$DECISION_PORT" >"$WORK/decision.log" 2>&1 &
DECISION_PID=$!

DASH="http://127.0.0.1:$DASHBOARD_PORT"
DECIDE="http://127.0.0.1:$DECISION_PORT"

if wait_for_http "$DASH/api/v1/health"; then
  printf '  %-46s PASS\n' "dashboard API starts"
else
  printf '  %-46s FAIL\n' "dashboard API starts"
  tail -n 20 "$WORK/dashboard.log" | sed 's/^/      /'
  FAILED=1
fi
if wait_for_http "$DECIDE/health"; then
  printf '  %-46s PASS\n' "decision API starts"
else
  printf '  %-46s FAIL\n' "decision API starts"
  tail -n 20 "$WORK/decision.log" | sed 's/^/      /'
  FAILED=1
fi

echo
echo "==> [6/6] Token separation"
expect_status "state without a token is refused"   401 "$DASH/api/v1/dashboard/state"
expect_status "state with the view token"          200 "$DASH/api/v1/dashboard/state" \
  -H "Authorization: Bearer $VIEW_TOKEN"
expect_status "admin command with the view token"  403 "$DASH/api/v1/control/command" \
  -X POST -H "Authorization: Bearer $VIEW_TOKEN" \
  -H 'Content-Type: application/json' -d '{"command":"PAUSE_NEW_ENTRIES"}'
expect_status "admin command with the admin token" 200 "$DASH/api/v1/control/command" \
  -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"command":"PAUSE_NEW_ENTRIES"}'

# The engine must consume the queued supervisory command on its next cycle.
check "engine applies the queued PAUSE command" "${RUN[@]}" run-once engine

echo
if [[ "$FAILED" -eq 0 ]]; then
  echo "Smoke test PASSED"
else
  echo "Smoke test FAILED" >&2
fi
exit "$FAILED"

#!/usr/bin/env bash
# Retention for Alpha-SPY runaway capture: the collector records full-universe
# quote snapshots every second around the clock (~20 GB/day of JSONL plus a
# comparable stream of snapshot_quotes rows). Without pruning it fills the
# disk in under a week and takes every service on the box down with it.
set -Eeuo pipefail
DB=/var/lib/alpha-spy/journal/alpha-spy.db
if [[ -d /var/lib/alpha-spy/market ]]; then
  find /var/lib/alpha-spy/market -name "*.jsonl" -mtime +2 -delete
fi
CUTOFF=$(date -u -d "3 days ago" +%Y-%m-%dT%H:%M:%S)
for i in $(seq 1 200); do
  N=$(sudo -u alphaspy sqlite3 "$DB" "DELETE FROM snapshot_quotes WHERE snapshot_id IN (SELECT snapshot_id FROM market_snapshots WHERE captured_at < \"$CUTOFF\" LIMIT 200); SELECT changes();" 2>/dev/null) || break
  [ "${N:-0}" -eq 0 ] && break
  sleep 0.2
done
sudo -u alphaspy sqlite3 "$DB" "DELETE FROM market_snapshots WHERE captured_at < \"$CUTOFF\"; DELETE FROM alerts WHERE timestamp < \"$CUTOFF\"; PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null
df -h / | tail -1

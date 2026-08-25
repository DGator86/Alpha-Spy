#!/usr/bin/env python3
"""Write a rolling ordinary-session event calendar Alpha-SPY can consume.

Alpha refuses every entry when events.json is missing (event_calendar_required).
No vendor feed is configured, so this generates the next several regular-session
windows. Macro/FOMC/CPI events are NOT invented here — add them to a real
source and point context.event_calendar_url at it.
"""
from __future__ import annotations
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
OUT = Path("/var/lib/alpha-spy/reference/ordinary-calendar.json")
# NYSE holidays that fall on weekdays in the near window; extend as needed.
HOLIDAYS = {"2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01"}


def main() -> None:
    now = datetime.now(EASTERN)
    events = []
    day = now.date()
    last_end = None
    for _ in range(14):
        if day.weekday() < 5 and day.isoformat() not in HOLIDAYS:
            start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=EASTERN).astimezone(UTC)
            end = datetime(day.year, day.month, day.day, 16, 0, tzinfo=EASTERN).astimezone(UTC)
            events.append({
                "type": "ordinary",
                "title": f"Regular session {day.isoformat()}",
                "starts_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ends_at": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            last_end = end
        day += timedelta(days=1)
    if not events or last_end is None:
        raise SystemExit("no ordinary sessions generated")
    generated = datetime.now(UTC)
    payload = {
        "schema_version": 1,
        "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_from": events[0]["starts_at"][:10] + "T00:00:00Z",
        "valid_through": (last_end + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "ordinary-session generator (no vendor feed configured)",
        "events": events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(OUT)
    print(f"wrote {len(events)} ordinary sessions to {OUT}")


if __name__ == "__main__":
    main()

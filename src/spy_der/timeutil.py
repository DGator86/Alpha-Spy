from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    value = value or utc_now()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def et_now() -> datetime:
    return utc_now().astimezone(ET)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute), tzinfo=ET)


def in_et_window(now: datetime, start: str, end: str) -> bool:
    local = now.astimezone(ET)
    start_t = parse_hhmm(start).replace(tzinfo=None)
    end_t = parse_hhmm(end).replace(tzinfo=None)
    current = local.time().replace(tzinfo=None)
    return start_t <= current <= end_t

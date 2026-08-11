from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import SuiteConfig


@dataclass(frozen=True)
class EventState:
    state: str
    source: str
    title: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    minutes_to_event: float | None = None
    blocked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _unknown(config: SuiteConfig, source: str) -> EventState:
    return EventState(
        state="unknown",
        source=source,
        blocked=bool(config.context.event_calendar_required),
    )


def event_state_at(config: SuiteConfig, at: datetime) -> EventState:
    """Resolve event risk only from a validated, time-bounded local calendar.

    A missing, stale or out-of-coverage calendar is UNKNOWN and blocks entries when
    event_calendar_required=true.  This is intentional: an empty file may not be
    interpreted as proof that no FOMC/CPI/earnings/rebalance event exists.
    """
    if not config.context.event_calendar_enabled:
        return EventState(state=config.market.event_state_default, source="disabled")
    path: Path = config.paths.event_calendar
    if not path.is_file():
        return _unknown(config, "missing_calendar")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _unknown(config, "invalid_calendar")
    if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
        return _unknown(config, "invalid_calendar")

    now = at.astimezone(UTC)
    generated = _parse(raw.get("generated_at"))
    valid_from = _parse(raw.get("valid_from"))
    valid_through = _parse(raw.get("valid_through"))
    if generated is None or valid_from is None or valid_through is None:
        return _unknown(config, "calendar_metadata_missing")
    if now - generated > timedelta(hours=config.context.event_calendar_max_age_hours):
        return _unknown(config, "calendar_stale")
    if not (valid_from <= now <= valid_through):
        return _unknown(config, "calendar_out_of_coverage")

    pre = timedelta(minutes=config.context.event_pre_minutes)
    post = timedelta(minutes=config.context.event_post_minutes)
    best: tuple[float, dict[str, Any], datetime, datetime] | None = None
    for event in raw["events"]:
        if not isinstance(event, dict):
            continue
        start = _parse(event.get("starts_at") or event.get("start"))
        end = _parse(event.get("ends_at") or event.get("end")) or start
        if start is None or end is None:
            continue
        if not (start - pre <= now <= end + post):
            continue
        distance = abs((start - now).total_seconds())
        if best is None or distance < best[0]:
            best = (distance, event, start, end)
    if best is None:
        return EventState(state="ordinary", source="calendar")

    _, event, start, end = best
    kind = str(event.get("type") or "unknown")
    allowed = {"macro_announcement", "earnings_heavy", "rebalance", "ordinary", "unknown"}
    if kind not in allowed:
        kind = "unknown"
    blocked = bool(
        (kind == "macro_announcement" and config.context.block_macro_event_entries)
        or (kind == "rebalance" and config.context.block_rebalance_entries)
        or (kind == "unknown" and config.context.event_calendar_required)
    )
    return EventState(
        state=kind,
        source="calendar",
        title=str(event.get("title") or "") or None,
        starts_at=start.isoformat().replace("+00:00", "Z"),
        ends_at=end.isoformat().replace("+00:00", "Z"),
        minutes_to_event=(start - now).total_seconds() / 60.0,
        blocked=blocked,
    )

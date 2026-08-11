from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import SuiteConfig

_ALLOWED = {"macro_announcement", "earnings_heavy", "rebalance", "ordinary", "unknown"}


def _parse(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def validate_event_calendar(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("event calendar must be a JSON object")
    events = raw.get("events")
    if not isinstance(events, list):
        raise ValueError("event calendar requires an events array")
    generated = _parse(raw.get("generated_at"))
    valid_from = _parse(raw.get("valid_from"))
    valid_through = _parse(raw.get("valid_through"))
    if valid_through <= valid_from:
        raise ValueError("valid_through must be after valid_from")
    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"events[{index}] must be an object")
        kind = str(event.get("type") or "unknown")
        if kind not in _ALLOWED:
            raise ValueError(f"events[{index}].type is unsupported: {kind}")
        start = _parse(event.get("starts_at") or event.get("start"))
        end = _parse(event.get("ends_at") or event.get("end") or start.isoformat())
        if end < start:
            raise ValueError(f"events[{index}] ends before it starts")
        normalized.append(
            {
                **event,
                "type": kind,
                "starts_at": start.isoformat().replace("+00:00", "Z"),
                "ends_at": end.isoformat().replace("+00:00", "Z"),
            }
        )
    return {
        **raw,
        "schema_version": int(raw.get("schema_version") or 1),
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "valid_from": valid_from.isoformat().replace("+00:00", "Z"),
        "valid_through": valid_through.isoformat().replace("+00:00", "Z"),
        "source": str(raw.get("source") or "operator_supplied"),
        "events": normalized,
    }


def refresh_event_calendar(config: SuiteConfig, source: str | None = None) -> dict[str, Any]:
    """Atomically refresh the versioned event-calendar input.

    Alpha-SPY is vendor-neutral here. `source` may be a local JSON file or HTTPS
    endpoint supplied by the operator/data vendor.  The live engine only consumes
    the validated local copy, never a remote response mid-decision.
    """
    source = str(source or config.context.event_calendar_url or "").strip()
    if not source:
        raise RuntimeError("event calendar source is not configured")
    if source.startswith("https://"):
        response = httpx.get(source, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        raw = response.json()
    elif source.startswith("http://"):
        raise RuntimeError("event calendar source must use HTTPS")
    else:
        raw = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
    normalized = validate_event_calendar(raw)
    destination = config.paths.event_calendar
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    text = json.dumps(normalized, indent=2, sort_keys=True) + "\n"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(destination)
    archive_dir = config.paths.state_root / "audit" / "event-calendars"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(normalized["generated_at"]).replace(":", "").replace("-", "")
    (archive_dir / f"events-{stamp}.json").write_text(text, encoding="utf-8")
    return normalized

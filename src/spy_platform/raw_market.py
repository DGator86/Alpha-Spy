from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EventType = Literal["QUOTE", "TRADE", "TIMESALE", "BAR", "OPTION_QUOTE", "SUMMARY", "UNIVERSE"]


def _iso(value: datetime | str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonical_event_id(
    *,
    source: str,
    event_type: EventType,
    symbol: str,
    event_timestamp: datetime | str,
    sequence: int | str | None,
    payload: dict[str, Any],
) -> str:
    material = {
        "source": str(source),
        "event_type": event_type,
        "symbol": str(symbol).upper(),
        "event_timestamp": _iso(event_timestamp),
        "sequence": sequence,
        "payload": payload,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    source: str
    event_type: EventType
    symbol: str
    event_timestamp: str
    received_timestamp: str
    sequence: int | str | None
    payload: dict[str, Any]
    schema_version: int = 1
    immutable: bool = True

    @classmethod
    def create(
        cls,
        *,
        source: str,
        event_type: EventType,
        symbol: str,
        event_timestamp: datetime | str,
        received_timestamp: datetime | str,
        payload: dict[str, Any],
        sequence: int | str | None = None,
    ) -> MarketEvent:
        event_time = _iso(event_timestamp)
        receive_time = _iso(received_timestamp)
        return cls(
            event_id=canonical_event_id(
                source=source,
                event_type=event_type,
                symbol=symbol,
                event_timestamp=event_time,
                sequence=sequence,
                payload=payload,
            ),
            source=str(source),
            event_type=event_type,
            symbol=str(symbol).upper(),
            event_timestamp=event_time,
            received_timestamp=receive_time,
            sequence=sequence,
            payload=dict(payload),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketFrame:
    frame_id: str
    as_of: str
    event_ids: tuple[str, ...]
    symbols: tuple[str, ...]
    source_watermarks: dict[str, str]
    quality: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    immutable: bool = True

    @classmethod
    def from_events(
        cls,
        *,
        as_of: datetime | str,
        events: list[MarketEvent],
        quality: dict[str, Any] | None = None,
    ) -> MarketFrame:
        stamp = _iso(as_of)
        event_ids = tuple(sorted(event.event_id for event in events))
        material = json.dumps(
            {"as_of": stamp, "event_ids": event_ids},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        watermarks: dict[str, str] = {}
        for event in events:
            current = watermarks.get(event.source)
            if current is None or event.event_timestamp > current:
                watermarks[event.source] = event.event_timestamp
        return cls(
            frame_id=hashlib.sha256(material).hexdigest(),
            as_of=stamp,
            event_ids=event_ids,
            symbols=tuple(sorted({event.symbol for event in events})),
            source_watermarks=watermarks,
            quality=dict(quality or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def clock_skew_seconds(event: MarketEvent) -> float:
    event_time = datetime.fromisoformat(event.event_timestamp.replace("Z", "+00:00"))
    receive_time = datetime.fromisoformat(event.received_timestamp.replace("Z", "+00:00"))
    return (receive_time - event_time).total_seconds()

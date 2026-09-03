from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .raw_market import EventType, MarketEvent


def _event_time(value: Any, *, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            raw = float(value)
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return fallback
        else:
            if raw > 10_000_000_000:
                raw /= 1000.0
            try:
                parsed = datetime.fromtimestamp(raw, tz=UTC)
            except (OSError, OverflowError, ValueError):
                return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_tradier_message(
    message: dict[str, Any],
    *,
    received_at: datetime | None = None,
) -> MarketEvent | None:
    """Normalize supported Tradier market messages into one canonical event.

    This accepts market data only. It contains no account/order handling.
    """
    received = received_at or datetime.now(UTC)
    raw_type = str(message.get("type") or "").lower()
    event_type: EventType
    if raw_type == "quote":
        event_type = "QUOTE"
    elif raw_type == "timesale":
        event_type = "TIMESALE"
    elif raw_type == "trade":
        event_type = "TRADE"
    elif raw_type == "summary":
        event_type = "SUMMARY"
    else:
        return None
    symbol = str(message.get("symbol") or "").upper()
    if not symbol:
        return None
    timestamp = _event_time(
        message.get("date")
        or message.get("timestamp")
        or message.get("trade_date")
        or message.get("biddate")
        or message.get("askdate"),
        fallback=received,
    )
    sequence = message.get("seq") or message.get("sequence")
    return MarketEvent.create(
        source="tradier",
        event_type=event_type,
        symbol=symbol,
        event_timestamp=timestamp,
        received_timestamp=received,
        sequence=sequence,
        payload=dict(message),
    )


def normalize_option_quote(
    row: dict[str, Any],
    *,
    underlying: str,
    expiration: str,
    captured_at: datetime,
) -> MarketEvent | None:
    symbol = str(row.get("symbol") or row.get("option_symbol") or "")
    if not symbol:
        strike = row.get("strike")
        right = row.get("right") or row.get("option_type")
        symbol = f"{underlying}:{expiration}:{right}:{strike}"
    payload = {
        **row,
        "underlying": underlying,
        "expiration": expiration,
    }
    return MarketEvent.create(
        source="tradier",
        event_type="OPTION_QUOTE",
        symbol=symbol,
        event_timestamp=captured_at,
        received_timestamp=captured_at,
        sequence=None,
        payload=payload,
    )


def normalize_minute_bar(
    row: dict[str, Any],
    *,
    source: str,
    symbol: str,
    timestamp: datetime | str,
    received_at: datetime | str,
) -> MarketEvent:
    return MarketEvent.create(
        source=source,
        event_type="BAR",
        symbol=symbol,
        event_timestamp=timestamp,
        received_timestamp=received_at,
        sequence=None,
        payload=dict(row),
    )

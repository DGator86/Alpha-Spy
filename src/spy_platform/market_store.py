from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from .raw_market import MarketEvent, MarketFrame

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS market_events (
    event_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    received_timestamp TEXT NOT NULL,
    sequence TEXT,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS market_events_time ON market_events(event_timestamp);
CREATE INDEX IF NOT EXISTS market_events_symbol_time ON market_events(symbol,event_timestamp);
CREATE INDEX IF NOT EXISTS market_events_type_time ON market_events(event_type,event_timestamp);

CREATE TABLE IF NOT EXISTS market_frames (
    frame_id TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS market_frames_asof ON market_frames(as_of);
"""


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class MarketEventStore:
    """Shared append-only substrate for Alpha, Beta, and Gamma.

    Duplicate canonical event IDs are ignored. There are intentionally no update or
    delete methods; data corrections arrive as new source events with new IDs.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        con.row_factory = sqlite3.Row
        return con

    def append(self, event: MarketEvent) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                """
                INSERT OR IGNORE INTO market_events(
                    event_id,source,event_type,symbol,event_timestamp,
                    received_timestamp,sequence,schema_version,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.source,
                    event.event_type,
                    event.symbol,
                    event.event_timestamp,
                    event.received_timestamp,
                    str(event.sequence) if event.sequence is not None else None,
                    event.schema_version,
                    _json(event.payload),
                ),
            )
            return cursor.rowcount == 1

    def append_many(self, events: Iterable[MarketEvent]) -> int:
        return sum(1 for event in events if self.append(event))

    def record_frame(self, frame: MarketFrame) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "INSERT OR IGNORE INTO market_frames(frame_id,as_of,schema_version,payload_json) VALUES(?,?,?,?)",
                (frame.frame_id, frame.as_of, frame.schema_version, _json(frame.as_dict())),
            )
            return cursor.rowcount == 1

    def events_until(
        self,
        *,
        as_of: str,
        symbols: set[str] | None = None,
        event_types: set[str] | None = None,
        after: str | None = None,
        limit: int = 100_000,
    ) -> list[MarketEvent]:
        clauses = ["event_timestamp <= ?"]
        params: list[object] = [as_of]
        if after is not None:
            clauses.append("event_timestamp > ?")
            params.append(after)
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(sorted(symbols))
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(sorted(event_types))
        params.append(int(limit))
        query = (
            "SELECT * FROM market_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY event_timestamp,event_id LIMIT ?"
        )
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [
            MarketEvent(
                event_id=row["event_id"],
                source=row["source"],
                event_type=row["event_type"],
                symbol=row["symbol"],
                event_timestamp=row["event_timestamp"],
                received_timestamp=row["received_timestamp"],
                sequence=row["sequence"],
                payload=json.loads(row["payload_json"]),
                schema_version=int(row["schema_version"]),
            )
            for row in rows
        ]

    def latest_event_time(self, source: str | None = None) -> str | None:
        query = "SELECT MAX(event_timestamp) AS latest FROM market_events"
        params: tuple[object, ...] = ()
        if source is not None:
            query += " WHERE source = ?"
            params = (source,)
        with self._connect() as con:
            row = con.execute(query, params).fetchone()
        return str(row["latest"]) if row and row["latest"] else None

    def count_events(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) AS count FROM market_events").fetchone()
        return int(row["count"]) if row else 0

    def source_age_seconds(self, *, source: str, now: datetime | None = None) -> float | None:
        latest = self.latest_event_time(source)
        if latest is None:
            return None
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        observed = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        return max(0.0, (current.astimezone(UTC) - observed.astimezone(UTC)).total_seconds())

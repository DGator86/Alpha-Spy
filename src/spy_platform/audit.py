from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .assertions import AnalystAssertion, ManagerView
from .contracts import DeltaState

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS delta_states (
    state_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyst_assertions (
    assertion_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    role TEXT NOT NULL,
    destination TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manager_views (
    view_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    manager TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    subject_id TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (subject_id, horizon_minutes, observed_at)
);
"""


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json(payload).encode()).hexdigest()


class AuditLedger:
    """Append-only evidence ledger for the processor organization.

    There are deliberately no update/delete methods. Corrections should be appended
    as new states/assertions/views so the original evidence remains auditable.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def record_delta(self, state: DeltaState) -> str:
        payload = state.as_dict()
        state_id = _hash(payload)
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO delta_states(state_id,timestamp,schema_version,payload_json) VALUES(?,?,?,?)",
                (state_id, state.timestamp, state.schema_version, _json(payload)),
            )
        return state_id

    def record_assertion(self, assertion: AnalystAssertion, *, destination: str) -> None:
        payload = assertion.as_dict()
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO analyst_assertions(assertion_id,timestamp,role,destination,payload_json) "
                "VALUES(?,?,?,?,?)",
                (
                    assertion.assertion_id,
                    assertion.timestamp,
                    assertion.role,
                    destination,
                    _json(payload),
                ),
            )

    def record_manager_view(self, view: ManagerView) -> None:
        payload = view.as_dict()
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO manager_views(view_id,timestamp,manager,payload_json) VALUES(?,?,?,?)",
                (view.view_id, view.timestamp, view.manager, _json(payload)),
            )

    def record_outcome(
        self,
        *,
        subject_id: str,
        horizon_minutes: int,
        observed_at: str,
        metrics: dict[str, Any],
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO outcomes(subject_id,horizon_minutes,observed_at,payload_json) "
                "VALUES(?,?,?,?)",
                (subject_id, int(horizon_minutes), observed_at, _json(metrics)),
            )

    def count(self, table: str) -> int:
        allowed = {"delta_states", "analyst_assertions", "manager_views", "outcomes"}
        if table not in allowed:
            raise ValueError(f"unsupported audit table: {table}")
        with self._connect() as con:
            row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0

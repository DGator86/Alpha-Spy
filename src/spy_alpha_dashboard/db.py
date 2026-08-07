from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS kv_state (
    key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    target_at TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    spy_price REAL NOT NULL,
    predicted_price REAL NOT NULL,
    predicted_low REAL NOT NULL,
    predicted_high REAL NOT NULL,
    probability_up REAL NOT NULL,
    actual_price REAL,
    direction_correct INTEGER,
    integrity TEXT NOT NULL,
    model_version TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_created ON predictions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_at DESC);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    command TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT NOT NULL DEFAULT '',
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status, created_at);
"""


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _initialize(self) -> None:
        with self._connect() as con:
            con.executescript(SCHEMA)

    def set_state(self, key: str, payload: dict[str, Any], updated_at: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO kv_state(key, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (key, json.dumps(payload, separators=(",", ":"), default=str), updated_at),
            )

    def get_state(self, key: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json FROM kv_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def upsert_prediction(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO predictions(
                    prediction_id, created_at, target_at, horizon_minutes,
                    spy_price, predicted_price, predicted_low, predicted_high,
                    probability_up, actual_price, direction_correct, integrity,
                    model_version, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_id) DO UPDATE SET
                    actual_price=excluded.actual_price,
                    direction_correct=excluded.direction_correct,
                    integrity=excluded.integrity,
                    payload_json=excluded.payload_json
                """,
                (
                    record["prediction_id"], record["created_at"], record["target_at"],
                    int(record.get("horizon_minutes", 15)), float(record["spy_price"]),
                    float(record["predicted_price"]), float(record["predicted_low"]),
                    float(record["predicted_high"]), float(record["probability_up"]),
                    record.get("actual_price"),
                    None if record.get("direction_correct") is None else int(bool(record["direction_correct"])),
                    record.get("integrity", "PENDING"), record.get("model_version", "unknown"),
                    json.dumps(record.get("payload", {}), separators=(",", ":"), default=str),
                ),
            )

    def apply_outcome(self, outcome: dict[str, Any]) -> bool:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT spy_price, predicted_price, payload_json FROM predictions WHERE prediction_id=?",
                (outcome["prediction_id"],),
            ).fetchone()
            if not row:
                return False
            actual = float(outcome["actual_price"])
            predicted_up = float(row["predicted_price"]) >= float(row["spy_price"])
            actual_up = actual >= float(row["spy_price"])
            payload = json.loads(row["payload_json"] or "{}")
            payload["outcome"] = outcome
            con.execute(
                """
                UPDATE predictions
                SET actual_price=?, direction_correct=?, integrity=?, payload_json=?
                WHERE prediction_id=?
                """,
                (
                    actual, int(predicted_up == actual_up), outcome.get("integrity", "VERIFIED"),
                    json.dumps(payload, separators=(",", ":"), default=str), outcome["prediction_id"],
                ),
            )
            return True

    def list_predictions(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["direction_correct"] = None if item["direction_correct"] is None else bool(item["direction_correct"])
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            out.append(item)
        return out

    def prediction_metrics(self, limit: int = 500) -> dict[str, Any]:
        rows = [r for r in self.list_predictions(limit) if r["actual_price"] is not None]
        if not rows:
            return {
                "sample_size": 0, "direction_accuracy": None, "brier": None,
                "range_coverage": None, "price_mae": None,
            }
        accuracy = sum(1 for r in rows if r["direction_correct"]) / len(rows)
        brier_terms = []
        cover = []
        abs_err = []
        for r in rows:
            actual_up = float(r["actual_price"]) >= float(r["spy_price"])
            p = float(r["probability_up"])
            brier_terms.append((p - (1.0 if actual_up else 0.0)) ** 2)
            cover.append(float(r["predicted_low"]) <= float(r["actual_price"]) <= float(r["predicted_high"]))
            abs_err.append(abs(float(r["actual_price"]) - float(r["predicted_price"])))
        return {
            "sample_size": len(rows),
            "direction_accuracy": accuracy,
            "brier": sum(brier_terms) / len(brier_terms),
            "range_coverage": sum(cover) / len(cover),
            "price_mae": sum(abs_err) / len(abs_err),
        }

    def add_alert(self, record: dict[str, Any]) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "INSERT INTO alerts(timestamp,severity,title,message,source) VALUES (?,?,?,?,?)",
                (record["timestamp"], record["severity"], record["title"], record["message"], record.get("source", "system")),
            )
            return int(cur.lastrowid)

    def list_alerts(self, limit: int = 100, include_acknowledged: bool = True) -> list[dict[str, Any]]:
        where = "" if include_acknowledged else "WHERE acknowledged=0"
        with self._connect() as con:
            rows = con.execute(
                f"SELECT * FROM alerts {where} ORDER BY timestamp DESC LIMIT ?", (max(1, min(limit, 1000)),)
            ).fetchall()
        return [{**dict(r), "acknowledged": bool(r["acknowledged"])} for r in rows]

    def acknowledge_alert(self, alert_id: int) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))
            return cur.rowcount > 0

    def queue_command(self, created_at: str, command: str, reason: str) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "INSERT INTO commands(created_at,command,reason,status) VALUES (?,?,?,'pending')",
                (created_at, command, reason),
            )
            return int(cur.lastrowid)

    def list_commands(self, limit: int = 100, pending_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE status='pending'" if pending_only else ""
        with self._connect() as con:
            rows = con.execute(
                f"SELECT * FROM commands {where} ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_command(self, command_id: int, status: str, message: str, completed_at: str | None) -> bool:
        if status not in {"pending", "acknowledged", "completed", "failed"}:
            raise ValueError("invalid command status")
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE commands SET status=?,message=?,completed_at=? WHERE id=?",
                (status, message, completed_at, command_id),
            )
            return cur.rowcount > 0

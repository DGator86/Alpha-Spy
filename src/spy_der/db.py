from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .timeutil import utc_iso

SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    exchange_state TEXT NOT NULL,
    spy_price REAL,
    spy_bid REAL,
    spy_ask REAL,
    covered_weight REAL NOT NULL,
    quote_count INTEGER NOT NULL,
    stale_quote_count INTEGER NOT NULL,
    integrity TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_time ON market_snapshots(captured_at DESC);

CREATE TABLE IF NOT EXISTS snapshot_quotes (
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    weight REAL NOT NULL,
    sector TEXT,
    price REAL,
    bid REAL,
    ask REAL,
    change_pct REAL,
    volume REAL,
    quote_timestamp TEXT,
    stale INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, symbol),
    FOREIGN KEY(snapshot_id) REFERENCES market_snapshots(snapshot_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_snapshot_quotes_symbol ON snapshot_quotes(symbol, snapshot_id);


CREATE TABLE IF NOT EXISTS option_chain_snapshots (
    chain_snapshot_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    underlying TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'strategy',
    expiration TEXT NOT NULL,
    underlying_price REAL,
    quote_count INTEGER NOT NULL,
    integrity TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_option_chain_time ON option_chain_snapshots(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_option_chain_purpose ON option_chain_snapshots(underlying, purpose, captured_at DESC);

CREATE TABLE IF NOT EXISTS option_quotes (
    chain_snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiration TEXT NOT NULL,
    strike REAL NOT NULL,
    right TEXT NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    midpoint REAL NOT NULL,
    volume INTEGER NOT NULL,
    open_interest INTEGER NOT NULL,
    iv REAL NOT NULL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(chain_snapshot_id, symbol),
    FOREIGN KEY(chain_snapshot_id) REFERENCES option_chain_snapshots(chain_snapshot_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_option_quotes_chain ON option_quotes(chain_snapshot_id, strike, right);


CREATE TABLE IF NOT EXISTS constituent_iv_observations (
    observation_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    weight REAL NOT NULL,
    expiration TEXT NOT NULL,
    dte INTEGER NOT NULL,
    underlying_price REAL NOT NULL,
    atm_iv REAL NOT NULL,
    put_25d_iv REAL,
    call_25d_iv REAL,
    skew REAL,
    quote_count INTEGER NOT NULL,
    integrity TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES market_snapshots(snapshot_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_constituent_iv_symbol_time
    ON constituent_iv_observations(symbol, captured_at DESC);

CREATE TABLE IF NOT EXISTS surface_metrics (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    reference_expiration TEXT,
    spy_iv REAL,
    constituent_iv REAL,
    vol_gap REAL,
    spy_skew REAL,
    constituent_skew REAL,
    skew_gap REAL,
    covered_weight REAL NOT NULL,
    symbol_count INTEGER NOT NULL,
    integrity TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES market_snapshots(snapshot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS features (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    breadth REAL NOT NULL,
    weighted_pressure REAL NOT NULL,
    concentration REAL NOT NULL,
    dispersion REAL NOT NULL,
    correlation REAL,
    downside_correlation REAL,
    weighted_return REAL NOT NULL,
    residual_pressure REAL NOT NULL,
    realized_vol REAL NOT NULL,
    trust_score REAL NOT NULL,
    health_state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES market_snapshots(snapshot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    target_at TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    formal_anchor INTEGER NOT NULL DEFAULT 0,
    spy_price REAL NOT NULL,
    predicted_price REAL NOT NULL,
    predicted_low REAL NOT NULL,
    predicted_high REAL NOT NULL,
    probability_up REAL NOT NULL,
    expected_return REAL NOT NULL,
    sigma_return REAL NOT NULL,
    regime TEXT NOT NULL,
    model_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    feature_hash TEXT NOT NULL,
    integrity TEXT NOT NULL DEFAULT 'PENDING',
    payload_json TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES market_snapshots(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_at);
CREATE INDEX IF NOT EXISTS idx_predictions_created ON predictions(created_at DESC);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    prediction_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL,
    probability_profit REAL NOT NULL,
    expected_value REAL NOT NULL,
    max_profit REAL NOT NULL,
    max_loss REAL NOT NULL,
    entry_price REAL NOT NULL,
    width REAL,
    expiration TEXT,
    rejection_reason TEXT,
    legs_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id)
);
CREATE INDEX IF NOT EXISTS idx_candidates_prediction ON candidates(prediction_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_time ON candidates(created_at DESC);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    prediction_id TEXT NOT NULL,
    candidate_id TEXT,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    allowed_risk REAL NOT NULL,
    trust_score REAL NOT NULL,
    health_state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id),
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions(created_at DESC);

CREATE TABLE IF NOT EXISTS orders (
    local_order_id TEXT PRIMARY KEY,
    decision_id TEXT,
    broker_order_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    environment TEXT NOT NULL,
    status TEXT NOT NULL,
    order_class TEXT NOT NULL,
    order_type TEXT NOT NULL,
    requested_price REAL,
    average_fill_price REAL,
    quantity INTEGER NOT NULL,
    previewed INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(decision_id) REFERENCES decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_time ON orders(created_at DESC);

CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    decision_id TEXT,
    broker_order_id TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    status TEXT NOT NULL,
    strategy TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_value REAL NOT NULL,
    current_value REAL,
    realized_pnl REAL,
    unrealized_pnl REAL,
    max_profit REAL NOT NULL,
    max_loss REAL NOT NULL,
    mfe REAL NOT NULL DEFAULT 0,
    mae REAL NOT NULL DEFAULT 0,
    exit_reason TEXT,
    legs_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(decision_id) REFERENCES decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, opened_at DESC);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    prediction_id TEXT PRIMARY KEY,
    confirmed_at TEXT NOT NULL,
    actual_price REAL NOT NULL,
    actual_high REAL,
    actual_low REAL,
    actual_return REAL NOT NULL,
    direction_correct INTEGER NOT NULL,
    price_error REAL NOT NULL,
    interval_covered INTEGER NOT NULL,
    brier REAL NOT NULL,
    integrity TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id)
);


CREATE TABLE IF NOT EXISTS data_revision_checks (
    prediction_id TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    frozen_price REAL NOT NULL,
    historical_price REAL,
    revision_bps REAL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id)
);

CREATE TABLE IF NOT EXISTS candidate_outcomes (
    candidate_id TEXT PRIMARY KEY,
    confirmed_at TEXT NOT NULL,
    mark_value REAL,
    pnl REAL,
    mfe REAL,
    mae REAL,
    target_hit INTEGER,
    stop_hit INTEGER,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    severity TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(timestamp DESC);

CREATE TABLE IF NOT EXISTS service_heartbeats (
    service TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL,
    detail TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS control_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_versions (
    version TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    session_date TEXT PRIMARY KEY,
    predictions INTEGER NOT NULL DEFAULT 0,
    matured_predictions INTEGER NOT NULL DEFAULT 0,
    direction_accuracy REAL,
    brier REAL,
    range_coverage REAL,
    price_mae REAL,
    trades INTEGER NOT NULL DEFAULT 0,
    net_pnl REAL NOT NULL DEFAULT 0,
    max_drawdown REAL NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);
"""


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=60, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=60000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """A connection that is actually closed when the block exits.

        sqlite3's own context manager commits or rolls back but never closes,
        so `with self.session() as con:` left every handle to the cyclic
        collector. That matters under WAL: a lingering reader holds a read
        lock that prevents checkpointing, so the -wal file grows without bound
        in a service that polls all session. It also leaks file descriptors
        until a collection happens to run.
        """
        con = self.connect()
        try:
            with con:
                yield con
        finally:
            con.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = self.connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    def initialize(self) -> None:
        with self.session() as con:
            con.executescript(SCHEMA)
            columns = {row[1] for row in con.execute("PRAGMA table_info(option_chain_snapshots)")}
            if "purpose" not in columns:
                con.execute(
                    "ALTER TABLE option_chain_snapshots ADD COLUMN purpose TEXT NOT NULL DEFAULT 'strategy'"
                )
            prediction_columns = {row[1] for row in con.execute("PRAGMA table_info(predictions)")}
            if "formal_anchor" not in prediction_columns:
                con.execute(
                    "ALTER TABLE predictions ADD COLUMN formal_anchor INTEGER NOT NULL DEFAULT 0"
                )
            con.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','2.0.0')"
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)

    def heartbeat(
        self,
        service: str,
        status: str = "ONLINE",
        latency_ms: float | None = None,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_iso()
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO service_heartbeats(service,updated_at,status,latency_ms,detail,payload_json)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(service) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    status=excluded.status,
                    latency_ms=excluded.latency_ms,
                    detail=excluded.detail,
                    payload_json=excluded.payload_json
                """,
                (service, now, status, latency_ms, detail, self._json(payload or {})),
            )

    def set_control(self, key: str, value: str) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO control_state(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                """,
                (key, value, utc_iso()),
            )

    def get_control(self, key: str, default: str = "") -> str:
        with self.session() as con:
            row = con.execute("SELECT value FROM control_state WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def alert(
        self,
        severity: str,
        title: str,
        message: str,
        source: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> int:
        with self.transaction() as con:
            cur = con.execute(
                "INSERT INTO alerts(timestamp,severity,source,title,message,payload_json) VALUES(?,?,?,?,?,?)",
                (utc_iso(), severity, source, title, message, self._json(payload or {})),
            )
            return int(cur.lastrowid)

    def insert_snapshot(self, snapshot: dict[str, Any], quotes: list[dict[str, Any]]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO market_snapshots(
                    snapshot_id,captured_at,exchange_state,spy_price,spy_bid,spy_ask,
                    covered_weight,quote_count,stale_quote_count,integrity,source,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot["snapshot_id"], snapshot["captured_at"], snapshot["exchange_state"],
                    snapshot.get("spy_price"), snapshot.get("spy_bid"), snapshot.get("spy_ask"),
                    snapshot["covered_weight"], snapshot["quote_count"], snapshot["stale_quote_count"],
                    snapshot["integrity"], snapshot["source"], self._json(snapshot.get("payload", {})),
                ),
            )
            con.executemany(
                """
                INSERT INTO snapshot_quotes(
                    snapshot_id,symbol,weight,sector,price,bid,ask,change_pct,volume,
                    quote_timestamp,stale,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        snapshot["snapshot_id"], q["symbol"], q.get("weight", 0.0), q.get("sector"),
                        q.get("price"), q.get("bid"), q.get("ask"), q.get("change_pct"),
                        q.get("volume"), q.get("quote_timestamp"), int(bool(q.get("stale", False))),
                        self._json(q.get("payload", {})),
                    )
                    for q in quotes
                ],
            )


    def insert_option_chain(self, chain: dict[str, Any], options: list[dict[str, Any]]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO option_chain_snapshots(
                    chain_snapshot_id,captured_at,underlying,purpose,expiration,underlying_price,
                    quote_count,integrity,source,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chain["chain_snapshot_id"], chain["captured_at"], chain["underlying"],
                    chain.get("purpose", "strategy"), chain["expiration"], chain.get("underlying_price"), len(options),
                    chain.get("integrity", "VERIFIED"), chain.get("source", "tradier"),
                    self._json(chain.get("payload", {})),
                ),
            )
            con.executemany(
                """
                INSERT INTO option_quotes(
                    chain_snapshot_id,symbol,expiration,strike,right,bid,ask,midpoint,volume,
                    open_interest,iv,delta,gamma,theta,vega,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        chain["chain_snapshot_id"], o["symbol"], o["expiration"], o["strike"],
                        o["right"], o["bid"], o["ask"], o["midpoint"], o["volume"],
                        o["open_interest"], o["iv"], o.get("delta"), o.get("gamma"),
                        o.get("theta"), o.get("vega"), self._json(o.get("payload", {})),
                    )
                    for o in options
                ],
            )

    def latest_option_chain(
        self, underlying: str = "SPY", purpose: str = "strategy"
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        with self.session() as con:
            row = con.execute(
                """
                SELECT * FROM option_chain_snapshots
                WHERE underlying=? AND purpose=?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (underlying, purpose),
            ).fetchone()
            if not row:
                return None, []
            options = con.execute(
                "SELECT * FROM option_quotes WHERE chain_snapshot_id=? ORDER BY right,strike",
                (row["chain_snapshot_id"],),
            ).fetchall()
        chain = dict(row)
        chain["payload"] = json.loads(chain.pop("payload_json") or "{}")
        result: list[dict[str, Any]] = []
        for option in options:
            item = dict(option)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return chain, result

    def snapshot_quotes(self, snapshot_id: str) -> list[dict[str, Any]]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM snapshot_quotes WHERE snapshot_id=? ORDER BY weight DESC",
                (snapshot_id,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["stale"] = bool(item["stale"])
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return output

    def insert_constituent_iv(self, observations: list[dict[str, Any]]) -> None:
        if not observations:
            return
        with self.transaction() as con:
            con.executemany(
                """
                INSERT INTO constituent_iv_observations(
                    observation_id,captured_at,snapshot_id,symbol,weight,expiration,dte,
                    underlying_price,atm_iv,put_25d_iv,call_25d_iv,skew,quote_count,
                    integrity,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        row["observation_id"], row["captured_at"], row["snapshot_id"],
                        row["symbol"], row.get("weight", 0.0), row["expiration"],
                        row.get("dte", 0), row["underlying_price"], row["atm_iv"],
                        row.get("put_25d_iv"), row.get("call_25d_iv"), row.get("skew"),
                        row.get("quote_count", 0), row.get("integrity", "VERIFIED"),
                        self._json(row.get("payload", {})),
                    )
                    for row in observations
                ],
            )

    def latest_constituent_iv(
        self, symbols: list[str], not_before_iso: str
    ) -> list[dict[str, Any]]:
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        sql = f"""
            SELECT o.*
            FROM constituent_iv_observations o
            JOIN (
                SELECT symbol, MAX(captured_at) AS captured_at
                FROM constituent_iv_observations
                WHERE symbol IN ({placeholders}) AND captured_at >= ?
                GROUP BY symbol
            ) latest
              ON latest.symbol=o.symbol AND latest.captured_at=o.captured_at
            ORDER BY o.weight DESC
        """
        with self.session() as con:
            rows = con.execute(sql, [*symbols, not_before_iso]).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return output

    def insert_surface_metrics(self, metrics: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO surface_metrics(
                    snapshot_id,created_at,reference_expiration,spy_iv,constituent_iv,
                    vol_gap,spy_skew,constituent_skew,skew_gap,covered_weight,
                    symbol_count,integrity,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    reference_expiration=excluded.reference_expiration,
                    spy_iv=excluded.spy_iv,
                    constituent_iv=excluded.constituent_iv,
                    vol_gap=excluded.vol_gap,
                    spy_skew=excluded.spy_skew,
                    constituent_skew=excluded.constituent_skew,
                    skew_gap=excluded.skew_gap,
                    covered_weight=excluded.covered_weight,
                    symbol_count=excluded.symbol_count,
                    integrity=excluded.integrity,
                    payload_json=excluded.payload_json
                """,
                (
                    metrics["snapshot_id"], metrics["created_at"],
                    metrics.get("reference_expiration"), metrics.get("spy_iv"),
                    metrics.get("constituent_iv"), metrics.get("vol_gap"),
                    metrics.get("spy_skew"), metrics.get("constituent_skew"),
                    metrics.get("skew_gap"), metrics.get("covered_weight", 0.0),
                    metrics.get("symbol_count", 0), metrics.get("integrity", "INCOMPLETE"),
                    self._json(metrics.get("payload", {})),
                ),
            )

    def surface_metrics(self, snapshot_id: str) -> dict[str, Any] | None:
        with self.session() as con:
            row = con.execute(
                "SELECT * FROM surface_metrics WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def insert_features(self, feature: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO features(
                    snapshot_id,created_at,breadth,weighted_pressure,concentration,dispersion,
                    correlation,downside_correlation,weighted_return,residual_pressure,
                    realized_vol,trust_score,health_state,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    feature["snapshot_id"], feature["created_at"], feature["breadth"],
                    feature["weighted_pressure"], feature["concentration"], feature["dispersion"],
                    feature.get("correlation"), feature.get("downside_correlation"),
                    feature["weighted_return"], feature["residual_pressure"], feature["realized_vol"],
                    feature["trust_score"], feature["health_state"], self._json(feature.get("payload", {})),
                ),
            )

    def insert_prediction(self, prediction: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO predictions(
                    prediction_id,snapshot_id,created_at,target_at,horizon_minutes,formal_anchor,spy_price,
                    predicted_price,predicted_low,predicted_high,probability_up,expected_return,
                    sigma_return,regime,model_version,config_hash,feature_hash,integrity,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    prediction["prediction_id"], prediction["snapshot_id"], prediction["created_at"],
                    prediction["target_at"], prediction["horizon_minutes"],
                    int(bool(prediction.get("formal_anchor", False))), prediction["spy_price"],
                    prediction["predicted_price"], prediction["predicted_low"], prediction["predicted_high"],
                    prediction["probability_up"], prediction["expected_return"], prediction["sigma_return"],
                    prediction["regime"], prediction["model_version"], prediction["config_hash"],
                    prediction["feature_hash"], prediction.get("integrity", "PENDING"),
                    self._json(prediction.get("payload", {})),
                ),
            )

    def insert_candidates(self, candidates: list[dict[str, Any]]) -> None:
        if not candidates:
            return
        with self.transaction() as con:
            con.executemany(
                """
                INSERT INTO candidates(
                    candidate_id,prediction_id,created_at,strategy,status,score,probability_profit,
                    expected_value,max_profit,max_loss,entry_price,width,expiration,rejection_reason,
                    legs_json,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        c["candidate_id"], c["prediction_id"], c["created_at"], c["strategy"],
                        c["status"], c["score"], c["probability_profit"], c["expected_value"],
                        c["max_profit"], c["max_loss"], c["entry_price"], c.get("width"),
                        c.get("expiration"), c.get("rejection_reason"), self._json(c.get("legs", [])),
                        self._json(c.get("payload", {})),
                    )
                    for c in candidates
                ],
            )

    def insert_decision(self, decision: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO decisions(
                    decision_id,prediction_id,candidate_id,created_at,action,reason,allowed_risk,
                    trust_score,health_state,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    decision["decision_id"], decision["prediction_id"], decision.get("candidate_id"),
                    decision["created_at"], decision["action"], decision["reason"],
                    decision["allowed_risk"], decision["trust_score"], decision["health_state"],
                    self._json(decision.get("payload", {})),
                ),
            )

    def insert_order(self, order: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO orders(
                    local_order_id,decision_id,broker_order_id,created_at,updated_at,environment,
                    status,order_class,order_type,requested_price,average_fill_price,quantity,
                    previewed,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    order["local_order_id"], order.get("decision_id"), order.get("broker_order_id"),
                    order["created_at"], order.get("updated_at", order["created_at"]), order["environment"],
                    order["status"], order["order_class"], order["order_type"], order.get("requested_price"),
                    order.get("average_fill_price"), order["quantity"], int(bool(order.get("previewed"))),
                    self._json(order.get("payload", {})),
                ),
            )

    def upsert_position(self, position: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO positions(
                    position_id,decision_id,broker_order_id,opened_at,closed_at,status,strategy,
                    quantity,entry_value,current_value,realized_pnl,unrealized_pnl,max_profit,max_loss,
                    mfe,mae,exit_reason,legs_json,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(position_id) DO UPDATE SET
                    closed_at=excluded.closed_at,status=excluded.status,current_value=excluded.current_value,
                    realized_pnl=excluded.realized_pnl,unrealized_pnl=excluded.unrealized_pnl,
                    mfe=excluded.mfe,mae=excluded.mae,exit_reason=excluded.exit_reason,
                    payload_json=excluded.payload_json
                """,
                (
                    position["position_id"], position.get("decision_id"), position.get("broker_order_id"),
                    position["opened_at"], position.get("closed_at"), position["status"], position["strategy"],
                    position["quantity"], position["entry_value"], position.get("current_value"),
                    position.get("realized_pnl"), position.get("unrealized_pnl"), position["max_profit"],
                    position["max_loss"], position.get("mfe", 0.0), position.get("mae", 0.0),
                    position.get("exit_reason"), self._json(position.get("legs", [])),
                    self._json(position.get("payload", {})),
                ),
            )

    def mature_prediction(self, outcome: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO prediction_outcomes(
                    prediction_id,confirmed_at,actual_price,actual_high,actual_low,actual_return,
                    direction_correct,price_error,interval_covered,brier,integrity,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    outcome["prediction_id"], outcome["confirmed_at"], outcome["actual_price"],
                    outcome.get("actual_high"), outcome.get("actual_low"), outcome["actual_return"],
                    int(bool(outcome["direction_correct"])), outcome["price_error"],
                    int(bool(outcome["interval_covered"])), outcome["brier"], outcome["integrity"],
                    self._json(outcome.get("payload", {})),
                ),
            )
            con.execute(
                "UPDATE predictions SET integrity=? WHERE prediction_id=?",
                (outcome["integrity"], outcome["prediction_id"]),
            )

    def insert_revision_check(self, check: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT INTO data_revision_checks(
                    prediction_id,checked_at,frozen_price,historical_price,revision_bps,
                    status,source,payload_json
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(prediction_id) DO UPDATE SET
                    checked_at=excluded.checked_at,
                    historical_price=excluded.historical_price,
                    revision_bps=excluded.revision_bps,
                    status=excluded.status,
                    source=excluded.source,
                    payload_json=excluded.payload_json
                """,
                (
                    check["prediction_id"], check["checked_at"], check["frozen_price"],
                    check.get("historical_price"), check.get("revision_bps"),
                    check["status"], check.get("source", "unknown"),
                    self._json(check.get("payload", {})),
                ),
            )

    def candidates_for_prediction(self, prediction_id: str) -> list[dict[str, Any]]:
        return self.latest_candidates(prediction_id, limit=1000)

    def insert_candidate_outcome(self, outcome: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO candidate_outcomes(
                    candidate_id,confirmed_at,mark_value,pnl,mfe,mae,target_hit,stop_hit,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    outcome["candidate_id"], outcome["confirmed_at"], outcome.get("mark_value"),
                    outcome.get("pnl"), outcome.get("mfe"), outcome.get("mae"),
                    None if outcome.get("target_hit") is None else int(bool(outcome["target_hit"])),
                    None if outcome.get("stop_hit") is None else int(bool(outcome["stop_hit"])),
                    self._json(outcome.get("payload", {})),
                ),
            )

    def nearest_option_chain(
        self, target_iso: str, purpose: str = "strategy", tolerance_seconds: int = 150
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        with self.session() as con:
            row = con.execute(
                """
                SELECT *, ABS((julianday(captured_at)-julianday(?))*86400.0) AS delta_seconds
                FROM option_chain_snapshots
                WHERE underlying='SPY' AND purpose=?
                  AND ABS((julianday(captured_at)-julianday(?))*86400.0) <= ?
                ORDER BY delta_seconds ASC LIMIT 1
                """,
                (target_iso, purpose, target_iso, tolerance_seconds),
            ).fetchone()
            if not row:
                return None, []
            options = con.execute(
                "SELECT * FROM option_quotes WHERE chain_snapshot_id=?",
                (row["chain_snapshot_id"],),
            ).fetchall()
        chain = dict(row)
        chain["payload"] = json.loads(chain.pop("payload_json") or "{}")
        output = []
        for option in options:
            item = dict(option)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return chain, output

    def option_chain_at_or_after(
        self, target_iso: str, purpose: str = "strategy", tolerance_seconds: int = 180
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Return the first executable option tape on or after a target time."""
        with self.session() as con:
            row = con.execute(
                """
                SELECT *, (julianday(captured_at)-julianday(?))*86400.0 AS delta_seconds
                FROM option_chain_snapshots
                WHERE underlying='SPY' AND purpose=? AND captured_at >= ?
                  AND (julianday(captured_at)-julianday(?))*86400.0 <= ?
                ORDER BY captured_at ASC LIMIT 1
                """,
                (target_iso, purpose, target_iso, target_iso, tolerance_seconds),
            ).fetchone()
            if not row:
                return None, []
            options = con.execute(
                "SELECT * FROM option_quotes WHERE chain_snapshot_id=?",
                (row["chain_snapshot_id"],),
            ).fetchall()
        chain = dict(row)
        chain["payload"] = json.loads(chain.pop("payload_json") or "{}")
        output = []
        for option in options:
            item = dict(option)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return chain, output

    def managed_daily_pnl(self, session_date: str) -> float:
        with self.session() as con:
            rows = con.execute(
                "SELECT closed_at,realized_pnl,status,unrealized_pnl FROM positions"
            ).fetchall()
        total = 0.0
        from datetime import datetime

        from .timeutil import ET
        for row in rows:
            if row["status"] == "OPEN":
                total += float(row["unrealized_pnl"] or 0.0)
                continue
            if not row["closed_at"]:
                continue
            try:
                closed = datetime.fromisoformat(str(row["closed_at"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if closed.astimezone(ET).date().isoformat() == session_date:
                total += float(row["realized_pnl"] or 0.0)
        return total

    def latest_snapshot(self) -> dict[str, Any] | None:
        with self.session() as con:
            row = con.execute(
                "SELECT * FROM market_snapshots ORDER BY captured_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def latest_features(self) -> dict[str, Any] | None:
        with self.session() as con:
            row = con.execute("SELECT * FROM features ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def latest_prediction(self) -> dict[str, Any] | None:
        with self.session() as con:
            row = con.execute("SELECT * FROM predictions ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def latest_candidates(self, prediction_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM candidates WHERE prediction_id=? ORDER BY score DESC LIMIT ?",
                (prediction_id, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["legs"] = json.loads(item.pop("legs_json") or "[]")
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def latest_decision(self) -> dict[str, Any] | None:
        with self.session() as con:
            row = con.execute("SELECT * FROM decisions ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def open_position(self) -> dict[str, Any] | None:
        with self.session() as con:
            row = con.execute(
                "SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["legs"] = json.loads(item.pop("legs_json") or "[]")
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def quote_history(self, symbol: str, limit: int = 120) -> list[dict[str, Any]]:
        with self.session() as con:
            rows = con.execute(
                """
                SELECT s.captured_at,q.price,q.bid,q.ask,q.change_pct,q.weight
                FROM snapshot_quotes q
                JOIN market_snapshots s ON s.snapshot_id=q.snapshot_id
                WHERE q.symbol=? AND q.price IS NOT NULL
                ORDER BY s.captured_at DESC LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def pending_predictions(self, now_iso: str, grace_seconds: int = 0) -> list[dict[str, Any]]:
        with self.session() as con:
            rows = con.execute(
                """
                SELECT p.* FROM predictions p
                LEFT JOIN prediction_outcomes o ON o.prediction_id=p.prediction_id
                WHERE o.prediction_id IS NULL AND p.target_at <= ?
                ORDER BY p.target_at ASC LIMIT 500
                """,
                (now_iso,),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return output

    def nearest_snapshot(self, target_iso: str, tolerance_seconds: int = 120) -> dict[str, Any] | None:
        with self.session() as con:
            row = con.execute(
                """
                SELECT *, ABS((julianday(captured_at)-julianday(?))*86400.0) AS delta_seconds
                FROM market_snapshots
                WHERE ABS((julianday(captured_at)-julianday(?))*86400.0) <= ?
                ORDER BY delta_seconds ASC LIMIT 1
                """,
                (target_iso, target_iso, tolerance_seconds),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def snapshot_at_or_after(
        self, target_iso: str, tolerance_seconds: int = 120
    ) -> dict[str, Any] | None:
        """Return the first frozen snapshot on or after the forecast target.

        This prevents the confirmation tape from accidentally using a pre-target
        snapshot merely because it is closer in absolute time.
        """
        with self.session() as con:
            row = con.execute(
                """
                SELECT *, (julianday(captured_at)-julianday(?))*86400.0 AS delta_seconds
                FROM market_snapshots
                WHERE captured_at >= ?
                  AND (julianday(captured_at)-julianday(?))*86400.0 <= ?
                ORDER BY captured_at ASC LIMIT 1
                """,
                (target_iso, target_iso, target_iso, tolerance_seconds),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def services(self) -> list[dict[str, Any]]:
        with self.session() as con:
            rows = con.execute("SELECT * FROM service_heartbeats ORDER BY service").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.session() as con:
            rows = con.execute("SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["acknowledged"] = bool(item["acknowledged"])
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def confirmation_metrics(
        self, limit: int = 500, formal_only: bool = True
    ) -> dict[str, Any]:
        with self.session() as con:
            if formal_only:
                rows = con.execute(
                    """
                    SELECT o.* FROM prediction_outcomes o
                    JOIN predictions p ON p.prediction_id=o.prediction_id
                    WHERE p.formal_anchor=1
                    ORDER BY o.confirmed_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM prediction_outcomes ORDER BY confirmed_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        if not rows:
            return {
                "sample_size": 0,
                "direction_accuracy": None,
                "brier": None,
                "range_coverage": None,
                "price_mae": None,
                "integrity_verified_pct": None,
            }
        n = len(rows)
        return {
            "sample_size": n,
            "direction_accuracy": sum(int(r["direction_correct"]) for r in rows) / n,
            "brier": sum(float(r["brier"]) for r in rows) / n,
            "range_coverage": sum(int(r["interval_covered"]) for r in rows) / n,
            "price_mae": sum(abs(float(r["price_error"])) for r in rows) / n,
            "integrity_verified_pct": sum(r["integrity"] == "VERIFIED" for r in rows) / n,
        }

    def integrity_check(self) -> str:
        with self.session() as con:
            row = con.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

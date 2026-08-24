"""Persistence behaviour the long-running services depend on.

Six services share one SQLite file for an entire session, so connection
lifetime and concurrent access are operational concerns, not micro-details.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from alpha_spy.config import SuiteConfig
from alpha_spy.dashboard.db import Repository
from alpha_spy.db import Journal

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.path.isdir("/proc/self/fd"),
    reason="file-descriptor counting needs /proc",
)


def _open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def make_config(tmp_path: Path) -> SuiteConfig:
    config = SuiteConfig()
    config.paths.state_root = tmp_path
    config.paths.database = tmp_path / "journal" / "alpha-spy.db"
    config.paths.dashboard_database = tmp_path / "dashboard" / "cc.sqlite"
    config.paths.universe_cache = tmp_path / "reference" / "universe.csv"
    config.paths.model_dir = tmp_path / "models"
    config.paths.report_dir = tmp_path / "reports"
    config.paths.log_dir = tmp_path / "logs"
    config.paths.production_sentinel = tmp_path / "PRODUCTION_UNLOCKED"
    config.create_directories()
    return config


@linux_only
def test_journal_reads_do_not_leak_connections(tmp_path):
    """Regression: `with connect() as con` commits but never closes.

    sqlite3's context manager does not close the handle, so every read left a
    connection for the cyclic collector. Under WAL a lingering reader holds a
    read lock that prevents checkpointing, so the -wal file grows all session.
    """
    journal = Journal(tmp_path / "j.db")
    for _ in range(20):  # warm up lazily-created handles
        journal.latest_snapshot()
    baseline = _open_fds()

    for _ in range(500):
        journal.latest_snapshot()
        journal.get_control("entries_paused", "false")
        journal.open_position()

    assert _open_fds() - baseline <= 2, "journal read paths leaked connections"


@linux_only
def test_dashboard_reads_do_not_leak_connections(tmp_path):
    repo = Repository(tmp_path / "d.sqlite")
    for _ in range(20):
        repo.get_state("live")
    baseline = _open_fds()

    for _ in range(500):
        repo.get_state("live")
        repo.list_alerts(5)

    assert _open_fds() - baseline <= 2, "dashboard read paths leaked connections"


@linux_only
def test_writes_do_not_leak_connections(tmp_path):
    journal = Journal(tmp_path / "j.db")
    for i in range(20):
        journal.set_control(f"warm{i}", "1")
    baseline = _open_fds()

    for i in range(300):
        journal.set_control(f"key{i}", str(i))

    assert _open_fds() - baseline <= 2, "journal write paths leaked connections"


def test_concurrent_writers_do_not_corrupt_the_journal(tmp_path):
    """The six services write to one file; WAL plus BEGIN IMMEDIATE must hold."""
    journal = Journal(tmp_path / "j.db")
    errors: list[Exception] = []

    def writer(worker: int) -> None:
        try:
            for i in range(40):
                journal.set_control(f"w{worker}-{i}", str(i))
                journal.heartbeat(f"svc{worker}", "ONLINE", 1.0, "ok")
        except Exception as exc:
            errors.append(exc)  # surfaced by the assertion below

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not errors, f"concurrent writes raised: {errors[:3]}"
    assert journal.integrity_check() == "ok"
    for worker in range(6):
        assert journal.get_control(f"w{worker}-39") == "39"


def test_integrity_check_reports_ok_on_a_fresh_database(tmp_path):
    assert Journal(tmp_path / "j.db").integrity_check() == "ok"


def test_insert_features_overwrites_the_same_snapshot(tmp_path):
    """Engine crash recovery: a retried cycle must not UNIQUE-fail on features.

    82.29.155.71 spent days alerting UNIQUE constraint failed: features.snapshot_id
    because the engine wrote features, then crashed in prediction, and never
    advanced last_engine_snapshot_id.
    """
    journal = Journal(tmp_path / "j.db")
    snapshot = {
        "snapshot_id": "S-retry",
        "captured_at": "2026-08-18T15:00:00Z",
        "exchange_state": "open",
        "spy_price": 768.0,
        "spy_bid": 767.99,
        "spy_ask": 768.01,
        "covered_weight": 1.0,
        "quote_count": 1,
        "stale_quote_count": 0,
        "integrity": "VERIFIED",
        "source": "test",
        "payload": {},
    }
    journal.insert_snapshot(snapshot, [{"symbol": "SPY", "price": 768.0, "bid": 767.99, "ask": 768.01}])
    feature = {
        "snapshot_id": "S-retry",
        "created_at": "2026-08-18T15:00:01Z",
        "breadth": 0.5,
        "weighted_pressure": 0.0,
        "concentration": 0.0,
        "dispersion": 0.0,
        "correlation": 0.0,
        "downside_correlation": 0.0,
        "weighted_return": 0.0,
        "residual_pressure": 0.0,
        "realized_vol": 0.01,
        "trust_score": 0.4,
        "health_state": "RED",
        "payload": {"pass": 1},
    }
    journal.insert_features(feature)
    feature["trust_score"] = 0.9
    feature["health_state"] = "GREEN"
    feature["payload"] = {"pass": 2}
    journal.insert_features(feature)
    stored = journal.latest_features()
    assert stored is not None
    assert stored["snapshot_id"] == "S-retry"
    assert stored["trust_score"] == 0.9
    assert stored["health_state"] == "GREEN"
    assert stored["payload"]["pass"] == 2


def test_matured_predictions_are_not_reselected(tmp_path):
    """The confirmation tape is append-once: a matured forecast never returns.

    pending_predictions excludes anything that already has an outcome row, so
    an outcome cannot be recomputed and overwritten on a later cycle.
    """
    from alpha_spy.services import EngineService, MarketService

    config = make_config(tmp_path)
    config.universe.source = "fallback"
    config.universe.minimum_symbols = 20
    journal = Journal(config.paths.database)

    MarketService(config, journal).run_once()
    EngineService(config, journal).run_once()

    prediction = journal.latest_prediction()
    assert prediction is not None

    far_future = "2099-01-01T00:00:00Z"
    assert any(
        p["prediction_id"] == prediction["prediction_id"]
        for p in journal.pending_predictions(far_future)
    )

    journal.mature_prediction(
        {
            "prediction_id": prediction["prediction_id"],
            "confirmed_at": "2026-08-07T00:00:00Z",
            "actual_price": float(prediction["spy_price"]),
            "actual_high": float(prediction["spy_price"]),
            "actual_low": float(prediction["spy_price"]),
            "actual_return": 0.0,
            "direction_correct": True,
            "price_error": 0.0,
            "interval_covered": True,
            "brier": 0.25,
            "integrity": "VERIFIED",
            "payload": {},
        }
    )

    assert all(
        p["prediction_id"] != prediction["prediction_id"]
        for p in journal.pending_predictions(far_future)
    )

"""Regression tests for the Friday Aug 21 fail-safes.

Each test is a real Friday failure mode. If it passes, that failure cannot
silently recur.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpha_spy.config import SuiteConfig
from alpha_spy.db import Journal
from alpha_spy.fail_safes import (
    IMPULSE_DOLLARS,
    MANAGEMENT_ERROR_FLATTEN,
    build_position_signal,
    condor_geometry_ok,
    impulse_allows_off_grid,
    must_fail_safe_flatten,
    prefer_defined_debits,
    reject_unsafe_condors,
    sandbox_reject_allows_paper_fallback,
    spot_impulse_from_entry,
)
from alpha_spy.features import compute_features
from alpha_spy.position_management import PositionSignal, evaluate_position
from alpha_spy.risk import AccountState, choose_decision


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


def test_position_signal_accepts_legacy_session_open_kwarg() -> None:
    signal = build_position_signal(
        forecast_return=-0.001,
        breadth=0.40,
        iv_edge_gap=0.0,
        spot=764.78,
        session_open=765.13,
        unexpected_future_field=True,
    )
    assert isinstance(signal, PositionSignal)
    assert signal.session_open == pytest.approx(765.13)
    assert signal.spot == pytest.approx(764.78)
    constructed = PositionSignal(
        forecast_return=0.0,
        breadth=0.5,
        iv_edge_gap=0.0,
        spot=100.0,
        session_open=99.0,
    )
    assert constructed.session_open == pytest.approx(99.0)


def test_fail_safe_flatten_fires_after_three_errors_or_clock() -> None:
    morning = datetime(2026, 8, 21, 14, 20, tzinfo=UTC)  # 10:20 ET
    close = datetime(2026, 8, 21, 19, 56, tzinfo=UTC)  # 15:56 ET
    after_four = datetime(2026, 8, 21, 20, 5, tzinfo=UTC)  # 16:05 ET
    assert must_fail_safe_flatten(
        now=morning, forced_flat_time_et="15:55", error_count=2
    ) is False
    assert must_fail_safe_flatten(
        now=morning, forced_flat_time_et="15:55", error_count=MANAGEMENT_ERROR_FLATTEN
    ) is True
    assert must_fail_safe_flatten(
        now=close, forced_flat_time_et="15:55", error_count=0
    ) is True
    assert must_fail_safe_flatten(
        now=after_four, forced_flat_time_et="15:55", error_count=0
    ) is True
    assert must_fail_safe_flatten(
        now=morning, forced_flat_time_et="15:55", error_count=0, flatten_requested=True
    ) is True
    friday_open = datetime(2026, 8, 21, 14, 15, tzinfo=UTC)
    sunday = datetime(2026, 8, 23, 15, 45, tzinfo=UTC)
    assert must_fail_safe_flatten(
        now=sunday,
        forced_flat_time_et="15:55",
        error_count=1,
        opened_at=friday_open,
    ) is True


def _condor_position(opened_at: datetime, entry_spot: float = 765.0) -> dict:
    return {
        "position_id": "POS-CONDOR",
        "opened_at": opened_at.isoformat().replace("+00:00", "Z"),
        "strategy": "IRON_CONDOR",
        "quantity": 1,
        "entry_value": 0.45,
        "max_profit": 45.0,
        "max_loss": 55.0,
        "mfe": 8.0,
        "mae": 0.0,
        "legs": [
            {"symbol": "P758", "right": "P", "strike": 758.0, "side": "buy_to_open", "quantity": 1},
            {"symbol": "P759", "right": "P", "strike": 759.0, "side": "sell_to_open", "quantity": 1},
            {"symbol": "C765", "right": "C", "strike": 765.0, "side": "sell_to_open", "quantity": 1},
            {"symbol": "C766", "right": "C", "strike": 766.0, "side": "buy_to_open", "quantity": 1},
        ],
        "payload": {
            "entry_kind": "credit",
            "management_state": {},
            "candidate": {
                "payload": {
                    "family": "short_vol",
                    "entry_spot": entry_spot,
                    "forecast_horizon_minutes": 15,
                }
            },
        },
    }


def test_condor_aborts_on_dollar_forty_impulse() -> None:
    opened = datetime(2026, 8, 21, 14, 17, tzinfo=UTC)
    now = opened + timedelta(minutes=36)
    signal = PositionSignal(
        forecast_return=0.002,
        breadth=0.62,
        iv_edge_gap=0.0,
        spot=766.45,
        session_open=765.13,
    )
    decision = evaluate_position(
        _condor_position(opened, entry_spot=764.78),
        now=now,
        pnl=8.0,
        mfe=8.0,
        signal=signal,
    )
    assert decision.should_exit is True
    assert decision.reason == "range_impulse_abort"
    assert spot_impulse_from_entry(764.78, 766.45) is True


def test_defined_debit_rejects_naked_long() -> None:
    candidates = [
        {"strategy": "LONG_PUT", "status": "ELIGIBLE", "width": None, "rejection_reason": None},
        {"strategy": "PUT_DEBIT_SPREAD", "status": "REJECTED", "width": 3.0, "rejection_reason": "edge_below_threshold"},
        {"strategy": "LONG_CALL", "status": "ELIGIBLE", "width": None, "rejection_reason": None},
        {"strategy": "CALL_DEBIT_SPREAD", "status": "ELIGIBLE", "width": 3.0, "rejection_reason": None},
    ]
    prefer_defined_debits(candidates)
    assert candidates[0]["status"] == "REJECTED"
    assert "defined_debit_preferred" in candidates[0]["rejection_reason"]
    assert candidates[2]["status"] == "REJECTED"
    assert candidates[3]["status"] == "ELIGIBLE"


def test_one_dollar_condor_against_spot_is_rejected() -> None:
    assert condor_geometry_ok(
        put_width=1.0,
        call_width=1.0,
        short_put=759.0,
        short_call=765.0,
        spot=764.78,
    ) is False
    assert condor_geometry_ok(
        put_width=2.0,
        call_width=2.0,
        short_put=762.0,
        short_call=768.0,
        spot=765.0,
    ) is True
    candidates = [
        {
            "strategy": "IRON_CONDOR",
            "status": "ELIGIBLE",
            "legs": [
                {"right": "P", "strike": 758.0, "side": "buy_to_open"},
                {"right": "P", "strike": 759.0, "side": "sell_to_open"},
                {"right": "C", "strike": 765.0, "side": "sell_to_open"},
                {"right": "C", "strike": 766.0, "side": "buy_to_open"},
            ],
        }
    ]
    reject_unsafe_condors(candidates, 764.78)
    assert candidates[0]["status"] == "REJECTED"


def test_off_grid_only_after_confirmed_impulse() -> None:
    assert impulse_allows_off_grid(
        session_high=764.90,
        session_low=764.25,
        spot=764.90,
        last_impulse_spot=None,
    ) is False
    assert impulse_allows_off_grid(
        session_high=765.70,
        session_low=764.25,
        spot=765.70,
        last_impulse_spot=None,
    ) is True
    assert impulse_allows_off_grid(
        session_high=767.79,
        session_low=764.25,
        spot=765.80,
        last_impulse_spot=765.70,
    ) is False
    assert impulse_allows_off_grid(
        session_high=767.79,
        session_low=764.25,
        spot=767.20,
        last_impulse_spot=765.70,
    ) is True
    assert IMPULSE_DOLLARS == pytest.approx(1.40)


def test_sandbox_400_can_fall_back_to_paper_only_in_paper_sandbox() -> None:
    assert sandbox_reject_allows_paper_fallback(
        paper_mode=True, environment="sandbox", status_code=400
    ) is True
    assert sandbox_reject_allows_paper_fallback(
        paper_mode=False, environment="sandbox", status_code=400
    ) is False
    assert sandbox_reject_allows_paper_fallback(
        paper_mode=True, environment="production", status_code=400
    ) is False


def test_calibration_fingerprint_ignores_risk_window_tweaks(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    before = config.calibration_fingerprint()
    decision_before = config.decision_fingerprint()
    config.risk.maximum_trades_per_day = 99
    config.risk.entry_stop_time_et = "15:10"
    config.strategy.max_width = 10.0
    assert config.calibration_fingerprint() == before
    assert config.decision_fingerprint() != decision_before


def test_empty_constituents_fail_closed_without_raising(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    snapshot = {
        "snapshot_id": "S-preopen",
        "captured_at": "2026-08-21T13:05:00Z",
        "covered_weight": 0.0,
        "quote_count": 1,
        "stale_quote_count": 0,
        "source": "tradier_production_stream",
        "integrity": "UNVERIFIED",
    }
    feature = compute_features(
        journal,
        config,
        snapshot,
        [{"symbol": "SPY", "price": 765.0, "weight": 0.0, "change_pct": 0.0}],
    )
    assert feature["health_state"] == "RED"
    assert feature["trust_score"] == pytest.approx(0.0)
    assert feature["payload"]["fail_closed_reason"] == "no_constituent_quotes"


def test_unmanageable_position_requests_flatten_and_blocks_entry(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    journal.upsert_position(
        {
            "position_id": "POS-STUCK",
            "decision_id": None,
            "broker_order_id": None,
            "opened_at": "2026-08-21T14:15:00Z",
            "closed_at": None,
            "status": "OPEN",
            "strategy": "LONG_PUT",
            "quantity": 1,
            "entry_value": 0.83,
            "current_value": 0.83,
            "realized_pnl": None,
            "unrealized_pnl": 0.0,
            "max_profit": 80.0,
            "max_loss": 83.0,
            "mfe": 0.0,
            "mae": 0.0,
            "exit_reason": None,
            "legs": [],
            "payload": {"management_error_count": 3, "entry_kind": "debit"},
        }
    )
    decision = choose_decision(
        config,
        journal,
        {"prediction_id": "P-1"},
        {"health_state": "GREEN", "trust_score": 1.0},
        [{"candidate_id": "C-1", "status": "ELIGIBLE", "max_loss": 50.0, "score": 1.0}],
        AccountState(100_000.0, 100_000.0, 100_000.0, 0.0),
        now=datetime(2026, 8, 21, 15, 0, tzinfo=UTC),
    )
    assert decision["action"] == "NO_TRADE"
    assert decision["reason"] == "managed_position_unmanageable"
    assert journal.get_control("flatten_requested") == "true"

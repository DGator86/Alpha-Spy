from __future__ import annotations

from pathlib import Path

import pytest

from spy_der.config import SuiteConfig
from spy_der.db import Journal
from spy_der.execution import build_multileg_payload
from spy_der.features import compute_features
from spy_der.prediction import create_prediction
from spy_der.services import ConfirmationService, DojoService, EngineService, MarketService, SettlementService
from spy_der.strategy import generate_candidates
from spy_der.tradier import normalize_quote


def make_config(tmp_path: Path) -> SuiteConfig:
    config = SuiteConfig()
    config.paths.state_root = tmp_path
    config.paths.database = tmp_path / "journal" / "suite-v2.db"
    config.paths.dashboard_database = tmp_path / "dashboard" / "command-center-v2.sqlite"
    config.paths.universe_cache = tmp_path / "reference" / "universe.csv"
    config.paths.model_dir = tmp_path / "models"
    config.paths.report_dir = tmp_path / "reports"
    config.paths.log_dir = tmp_path / "logs"
    config.paths.production_sentinel = tmp_path / "PRODUCTION_UNLOCKED"
    config.universe.source = "fallback"
    config.universe.minimum_symbols = 20
    config.universe.minimum_covered_weight = 0.90
    config.dashboard.require_view_token = False
    config.create_directories()
    return config


def test_tradier_change_percentage_and_timestamp_normalization() -> None:
    row = normalize_quote(
        {
            "symbol": "SPY",
            "last": 640.0,
            "prevclose": 638.0,
            "change_percentage": 0.50,
            "trade_date": 1_754_000_000_000,
        }
    )
    assert row["change_pct"] == pytest.approx(0.005)
    assert row["quote_timestamp"].endswith("Z")


def test_demo_market_engine_and_surface_pipeline(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    market = MarketService(config, journal)

    snapshot_id = market.run_once()
    snapshot = journal.latest_snapshot()
    assert snapshot and snapshot["snapshot_id"] == snapshot_id
    assert snapshot["integrity"] == "VERIFIED"
    assert snapshot["covered_weight"] == pytest.approx(1.0)

    surface = journal.surface_metrics(snapshot_id)
    assert surface is not None
    assert surface["spy_iv"] > surface["constituent_iv"]
    assert surface["integrity"] == "VERIFIED"

    result = EngineService(config, journal).run_once()
    assert result and "prediction=" in result
    assert journal.latest_prediction() is not None
    assert journal.latest_features()["health_state"] == "GREEN"
    assert journal.integrity_check() == "ok"


def test_strategy_butterfly_consolidates_middle_leg(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    prediction = {
        "prediction_id": "P-TEST",
        "feature_hash": "12345678" + "0" * 56,
        "spy_price": 100.0,
        "predicted_price": 100.0,
        "predicted_low": 98.0,
        "predicted_high": 102.0,
        "expected_return": 0.0,
        "sigma_return": 0.01,
    }
    options = []
    for right in ("C", "P"):
        for strike in range(95, 106):
            options.append(
                {
                    "symbol": f"SPYTEST{right}{strike}",
                    "expiration": "2026-08-07",
                    "strike": float(strike),
                    "right": right,
                    "bid": 1.00,
                    "ask": 1.02,
                    "midpoint": 1.01,
                    "volume": 100,
                    "open_interest": 1000,
                    "iv": 0.20,
                    "delta": 0.25 if right == "C" else -0.25,
                }
            )
    config.strategy.min_probability = 0.0
    config.strategy.min_edge_dollars = -100.0
    config.risk.maximum_trade_risk_dollars = 10_000.0
    candidates = generate_candidates(config, prediction, options)
    butterfly = next(c for c in candidates if c["strategy"] == "CALL_BUTTERFLY")
    assert len(butterfly["legs"]) == 3
    middle = next(leg for leg in butterfly["legs"] if leg["side"] == "sell_to_open")
    assert middle["quantity"] == 2

    payload = build_multileg_payload(butterfly, quantity=1, price=butterfly["entry_price"])
    middle_index = next(
        index for index in range(3) if payload[f"side[{index}]"] == "sell_to_open"
    )
    assert payload[f"quantity[{middle_index}]"] == 2


def test_live_trading_remains_fail_closed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.trading.enabled = True
    config.trading.paper_mode = False
    config.trading.submit_orders = True
    config.tradier.environment = "production"
    with pytest.raises(RuntimeError, match="Live trading locked"):
        config.assert_live_trading_safe()



def test_confirmation_tape_and_dojo_report(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.audit.maturity_grace_seconds = 0
    journal = Journal(config.paths.database)
    market = MarketService(config, journal)
    market.run_once()
    EngineService(config, journal).run_once()
    prediction = journal.latest_prediction()
    assert prediction is not None

    market.run_once()
    realized = journal.latest_snapshot()
    assert realized is not None
    with journal.transaction() as con:
        con.execute(
            "UPDATE predictions SET target_at=?, formal_anchor=1 WHERE prediction_id=?",
            (realized["captured_at"], prediction["prediction_id"]),
        )

    matured = ConfirmationService(config, journal).run_once()
    assert matured == 1
    metrics = journal.confirmation_metrics(formal_only=True)
    assert metrics["sample_size"] == 1
    assert metrics["integrity_verified_pct"] == pytest.approx(1.0)

    with journal.connect() as con:
        revision = con.execute(
            "SELECT status FROM data_revision_checks WHERE prediction_id=?",
            (prediction["prediction_id"],),
        ).fetchone()
        candidate_outcomes = con.execute(
            "SELECT COUNT(*) FROM candidate_outcomes"
        ).fetchone()[0]
    assert revision and revision["status"] == "VERIFIED"
    assert candidate_outcomes > 0

    output = DojoService(config, journal).run_once(recent_days=20)
    report = __import__("json").loads((output / "summary.json").read_text())
    assert report["formal_confirmation_metrics"]["sample_size"] == 1
    assert report["governance"]["live_model_changed"] is False
    assert report["promotion_status"]["automatic_promotion"] is False


def test_confirmation_snapshot_never_uses_pre_target_tape(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    base = {
        "exchange_state": "open",
        "covered_weight": 1.0,
        "quote_count": 1,
        "stale_quote_count": 0,
        "integrity": "VERIFIED",
        "source": "demo",
        "payload": {},
        "spy_bid": 99.99,
        "spy_ask": 100.01,
    }
    journal.insert_snapshot(
        {**base, "snapshot_id": "PRE", "captured_at": "2026-08-06T14:59:59Z", "spy_price": 100.0},
        [{"symbol": "SPY", "price": 100.0, "bid": 99.99, "ask": 100.01}],
    )
    journal.insert_snapshot(
        {**base, "snapshot_id": "POST", "captured_at": "2026-08-06T15:00:20Z", "spy_price": 100.2},
        [{"symbol": "SPY", "price": 100.2, "bid": 100.19, "ask": 100.21}],
    )
    realized = journal.snapshot_at_or_after("2026-08-06T15:00:00Z", tolerance_seconds=60)
    assert realized is not None
    assert realized["snapshot_id"] == "POST"
    assert realized["delta_seconds"] == pytest.approx(20.0, abs=0.1)

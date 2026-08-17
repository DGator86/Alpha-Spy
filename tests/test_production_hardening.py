from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from alpha_spy.config import SuiteConfig
from alpha_spy.context import build_market_context
from alpha_spy.db import Journal
from alpha_spy.event_calendar import refresh_event_calendar
from alpha_spy.events import event_state_at
from alpha_spy.execution import ExecutionManager
from alpha_spy.hardening import _on_entry_grid
from alpha_spy.position_management import PositionSignal, evaluate_position
from alpha_spy.replay import ReplayVerifier
from alpha_spy.risk import AccountState, allowed_risk, choose_decision, parse_account_state
from alpha_spy.strategy import generate_candidates
from alpha_spy.streaming_market import StreamingMarketService
from alpha_spy.tradier import TradierClient, TradierError
from alpha_spy.validation import PromotionEvaluator


def make_config(tmp_path: Path) -> SuiteConfig:
    config = SuiteConfig()
    config.paths.state_root = tmp_path
    config.paths.database = tmp_path / "journal" / "alpha-spy.db"
    config.paths.dashboard_database = tmp_path / "dashboard" / "command-center.sqlite"
    config.paths.universe_cache = tmp_path / "reference" / "universe.csv"
    config.paths.model_dir = tmp_path / "models"
    config.paths.report_dir = tmp_path / "reports"
    config.paths.log_dir = tmp_path / "logs"
    config.paths.production_sentinel = tmp_path / "PRODUCTION_UNLOCKED"
    config.paths.production_approval = tmp_path / "PRODUCTION_APPROVED.json"
    config.paths.event_calendar = tmp_path / "events.json"
    config.paths.promotion_report = tmp_path / "reports" / "promotion-latest.json"
    config.universe.minimum_symbols = 20
    config.create_directories()
    return config


def test_sandbox_broker_submission_is_distinct_from_production_unlock(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.environment = "sandbox"
    config.tradier.access_token = SecretStr("sandbox-token")
    config.tradier.market_access_token = SecretStr("market-token")
    config.tradier.account_id = "SANDBOX"
    config.trading.enabled = True
    config.trading.paper_mode = True
    config.trading.submit_orders = True
    config.assert_broker_submission_safe()  # no production sentinel or approval required


def test_production_broker_submission_requires_evidence_approval(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.environment = "production"
    config.tradier.access_token = SecretStr("production-token")
    config.tradier.account_id = "LIVE"
    config.trading.enabled = True
    config.trading.paper_mode = False
    config.trading.submit_orders = True
    config.paths.production_sentinel.write_text("unlocked", encoding="utf-8")
    with pytest.raises(RuntimeError, match="production approval"):
        config.assert_broker_submission_safe()


def test_missing_broker_account_payload_is_invalid_for_risk(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    account = parse_account_state({})
    assert account.equity == pytest.approx(25_000.0)  # display compatibility
    assert account.valid is False
    assert allowed_risk(config, account, 1.0, "GREEN") == 0.0


def _options(expiration: str) -> list[dict]:
    rows = []
    for right in ("C", "P"):
        for strike in range(96, 105):
            intrinsic = max(100.0 - strike, 0.0) if right == "C" else max(strike - 100.0, 0.0)
            mid = max(0.40, intrinsic + 0.55)
            rows.append(
                {
                    "symbol": f"SPYTEST{expiration.replace('-', '')}{right}{strike}",
                    "expiration": expiration,
                    "strike": float(strike),
                    "right": right,
                    "bid": mid - 0.02,
                    "ask": mid + 0.02,
                    "midpoint": mid,
                    "volume": 1000,
                    "open_interest": 5000,
                    "iv": 0.25,
                    "delta": 0.50 if right == "C" else -0.50,
                    "gamma": 0.02,
                }
            )
    return rows


def test_candidate_valuation_preserves_remaining_0dte_time_value(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.strategy.min_probability = 0.0
    config.strategy.min_edge_dollars = -1000.0
    config.strategy.max_debit = 100.0
    config.risk.maximum_trade_risk_dollars = 100_000.0
    created = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)  # 10:00 ET
    target = created + timedelta(minutes=15)
    prediction = {
        "prediction_id": "P-HORIZON",
        "feature_hash": "12345678" + "0" * 56,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "target_at": target.isoformat().replace("+00:00", "Z"),
        "horizon_minutes": 15,
        "spy_price": 100.0,
        "predicted_price": 100.0,
        "predicted_low": 98.5,
        "predicted_high": 101.5,
        "expected_return": 0.0,
        "sigma_return": 0.006,
        "payload": {"input_health": {"required_ok": True}},
    }
    candidates = generate_candidates(config, prediction, _options("2026-08-10"))
    call = next(c for c in candidates if c["strategy"] == "LONG_CALL")
    assert call["payload"]["valuation_method"] == "P-Q-horizon-reprice-cost-stress-v3.0"
    assert call["payload"]["horizon_tenor_years"] > 0
    strike = next(leg["strike"] for leg in call["legs"] if leg["right"] == "C")
    intrinsic_now = max(100.0 - strike, 0.0)
    assert call["payload"]["q_structure_value"] > intrinsic_now


def _position(opened_at: datetime) -> dict:
    return {
        "position_id": "POS-1",
        "opened_at": opened_at.isoformat().replace("+00:00", "Z"),
        "strategy": "LONG_CALL",
        "quantity": 1,
        "entry_value": 1.00,
        "max_profit": 300.0,
        "max_loss": 105.0,
        "mfe": 0.0,
        "mae": 0.0,
        "legs": [
            {
                "symbol": "SPYTEST",
                "right": "C",
                "strike": 100.0,
                "side": "buy_to_open",
                "quantity": 1,
            }
        ],
        "payload": {
            "entry_kind": "debit",
            "management_state": {},
            "candidate": {
                "payload": {
                    "family": "directional_long",
                    "entry_spot": 100.0,
                    "forecast_sigma_return": 0.006,
                    "forecast_expected_return": 0.001,
                    "forecast_horizon_minutes": 15,
                    "profit_unbounded": True,
                }
            },
        },
    }


def test_professional_stop_tightens_and_horizon_is_hard_exit() -> None:
    now = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
    signal = PositionSignal(forecast_return=0.001, breadth=0.60, iv_edge_gap=0.0, spot=100.10)
    early = evaluate_position(
        _position(now - timedelta(minutes=2)), now=now, pnl=0.0, mfe=0.0, signal=signal
    )
    late = evaluate_position(
        _position(now - timedelta(minutes=12)), now=now, pnl=0.0, mfe=0.0, signal=signal
    )
    assert early.stop_pnl == pytest.approx(-50.0)
    assert late.stop_pnl == pytest.approx(-33.0)

    horizon = evaluate_position(
        _position(now - timedelta(minutes=16)), now=now, pnl=5.0, mfe=5.0, signal=signal
    )
    assert horizon.should_exit is True
    assert horizon.reason == "forecast_horizon_exit"


def test_five_minute_entry_grid_is_deterministic(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert _on_entry_grid(config, "2026-08-10T13:40:17Z") is True  # 09:40 ET
    assert _on_entry_grid(config, "2026-08-10T13:41:17Z") is False


def test_tradier_change_endpoint_refuses_multileg_debit_type(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.access_token = SecretStr("x")
    config.tradier.account_id = "A"
    client = TradierClient(config)
    try:
        with pytest.raises(TradierError, match="cancel/replace"):
            client.change_order(1, order_type="debit", duration="day", price=1.00)
    finally:
        client.close()


def test_partial_fill_is_adopted_as_managed_position(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    config.tradier.environment = "sandbox"
    config.tradier.access_token = SecretStr("sandbox-token")
    config.tradier.market_access_token = SecretStr("market-token")
    config.tradier.account_id = "SANDBOX"
    config.trading.enabled = True
    config.trading.paper_mode = True
    config.trading.submit_orders = True
    config.trading.limit_price_steps = 0
    config.trading.limit_price_wait_seconds = 1
    config.trading.cancel_confirm_seconds = 1
    journal = Journal(config.paths.database)

    class FakeClient:
        def __init__(self, _config):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def preview_order(self, payload):
            return {"result": True, "status": "ok", "commission": 0.65, "fees": 0.0}

        def place_order(self, payload):
            return {"id": 123}

        def order(self, order_id):
            self.calls += 1
            if self.calls == 1:
                return {
                    "id": order_id,
                    "status": "partially_filled",
                    "avg_fill_price": 1.00,
                    "exec_quantity": 1,
                    "remaining_quantity": 0,
                }
            return {
                "id": order_id,
                "status": "canceled",
                "avg_fill_price": 1.00,
                "exec_quantity": 1,
                "remaining_quantity": 0,
            }

        def cancel_order(self, order_id):
            return {"id": order_id, "status": "pending_cancel"}

        def change_order(self, *args, **kwargs):
            raise AssertionError("should not reprice after exposure exists")

    monkeypatch.setattr("alpha_spy.execution.TradierClient", FakeClient)
    manager = ExecutionManager(config, journal)
    decision = {"decision_id": None, "action": "SUBMIT_ORDER"}
    candidate = {
        "candidate_id": "C-1",
        "entry_price": 1.00,
        "entry_kind": "debit",
        "strategy": "LONG_CALL",
        "max_profit": 500.0,
        "max_loss": 100.0,
        "legs": [
            {
                "symbol": "SPY260810C00100000",
                "side": "buy_to_open",
                "quantity": 1,
                "right": "C",
                "strike": 100.0,
            }
        ],
        "payload": {"family": "directional_long"},
    }
    order = manager.execute(decision, candidate)
    assert order["payload"]["exec_quantity"] == pytest.approx(1.0)
    position = journal.open_position()
    assert position is not None
    assert position["quantity"] == 1
    assert position["payload"]["entry_fees"] == pytest.approx(0.65)


def test_market_and_execution_credentials_are_separate(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.environment = "sandbox"
    config.tradier.access_token = SecretStr("paper-token")
    config.tradier.market_access_token = SecretStr("market-token")
    config.tradier.account_id = "VIRTUAL"

    execution = TradierClient(config)
    market = TradierClient(config, purpose="market")
    try:
        assert execution.base_url == "https://sandbox.tradier.com/v1"
        assert execution.token == "paper-token"
        assert execution.account_id == "VIRTUAL"
        assert market.base_url == "https://api.tradier.com/v1"
        assert market.token == "market-token"
        assert market.account_id == ""
    finally:
        execution.close()
        market.close()


def test_paper_mode_can_submit_to_sandbox_broker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.environment = "sandbox"
    config.tradier.access_token = SecretStr("paper-token")
    config.tradier.market_access_token = SecretStr("market-token")
    config.tradier.account_id = "VIRTUAL"
    config.trading.enabled = True
    config.trading.paper_mode = True
    config.trading.submit_orders = True
    config.assert_broker_submission_safe()

    journal = Journal(config.paths.database)
    now = datetime(2026, 8, 10, 13, 40, tzinfo=UTC)
    prediction = {"prediction_id": "P-PAPER"}
    feature = {"health_state": "GREEN", "trust_score": 1.0}
    candidate = {
        "candidate_id": "C-PAPER",
        "status": "ELIGIBLE",
        "max_loss": 50.0,
        "score": 1.0,
    }
    decision = choose_decision(
        config,
        journal,
        prediction,
        feature,
        [candidate],
        AccountState(100_000.0, 100_000.0, 100_000.0, 0.0),
        now=now,
    )
    assert decision["action"] == "SUBMIT_ORDER"


def test_sandbox_submission_rejects_non_paper_mode(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.environment = "sandbox"
    config.tradier.access_token = SecretStr("paper-token")
    config.tradier.market_access_token = SecretStr("market-token")
    config.tradier.account_id = "VIRTUAL"
    config.trading.enabled = True
    config.trading.paper_mode = False
    config.trading.submit_orders = True
    with pytest.raises(RuntimeError, match="paper_mode=true"):
        config.assert_broker_submission_safe()


def test_stream_event_updates_model_cache_without_using_sandbox_data(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.market_access_token = SecretStr("market-token")
    journal = Journal(config.paths.database)
    service = StreamingMarketService(config, journal)
    service._cache["SPY"] = {
        "symbol": "SPY",
        "price": 100.0,
        "bid": 99.99,
        "ask": 100.01,
        "change_pct": 0.0,
        "volume": 1.0,
        "quote_timestamp": "2026-08-10T13:40:00Z",
        "weight": 0.0,
        "sector": "ETF",
        "stale": False,
        "payload": {},
    }
    service._previous_close["SPY"] = 99.0
    service._ingest_payload(
        '{"type":"timesale","symbol":"SPY","bid":"100.10","ask":"100.12",'
        '"last":"100.11","size":"100","date":"1786369201000"}'
    )
    assert service._cache["SPY"]["price"] == pytest.approx(100.11)
    assert service._cache["SPY"]["bid"] == pytest.approx(100.10)
    assert service._cache["SPY"]["change_pct"] == pytest.approx(100.11 / 99.0 - 1.0)
    assert service.config.tradier.environment == "production"


def test_stream_counts_timesale_prints_once(tmp_path: Path) -> None:
    """trade + timesale describe the same print; only timesale may accumulate OFI."""
    config = make_config(tmp_path)
    config.tradier.market_access_token = SecretStr("market-token")
    journal = Journal(config.paths.database)
    service = StreamingMarketService(config, journal)
    service._cache["SPY"] = {
        "symbol": "SPY",
        "price": 100.0,
        "bid": 99.99,
        "ask": 100.01,
        "change_pct": 0.0,
        "volume": 1.0,
        "quote_timestamp": "2026-08-10T13:40:00Z",
        "weight": 0.0,
        "sector": "ETF",
        "stale": False,
        "payload": {},
    }
    service._ingest_payload(
        '{"type":"trade","symbol":"SPY","price":"100.12","size":"250","cvol":"1000","date":"1786369201000"}'
    )
    service._ingest_payload(
        '{"type":"timesale","symbol":"SPY","bid":"100.10","ask":"100.12",'
        '"last":"100.12","size":"250","date":"1786369201000"}'
    )
    micro = service._micro_snapshot()["SPY"]
    assert micro["trade_count"] == 1.0
    assert micro["trade_volume"] == pytest.approx(250.0)
    assert micro["buy_volume"] == pytest.approx(250.0)


def test_event_calendar_is_required_and_time_bounded(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    at = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    # Missing is not silently ordinary.
    missing = event_state_at(config, at)
    assert missing.state == "unknown"
    assert missing.blocked is True

    config.paths.event_calendar.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-08-10T13:30:00Z",
                "valid_from": "2026-08-10T00:00:00Z",
                "valid_through": "2026-08-11T23:59:59Z",
                "source": "test",
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    ordinary = event_state_at(config, at)
    assert ordinary.state == "ordinary"
    assert ordinary.blocked is False

    raw = json.loads(config.paths.event_calendar.read_text())
    raw["generated_at"] = "2026-08-01T00:00:00Z"
    config.paths.event_calendar.write_text(json.dumps(raw), encoding="utf-8")
    stale = event_state_at(config, at)
    assert stale.state == "unknown"
    assert stale.source == "calendar_stale"
    assert stale.blocked is True


def test_event_calendar_refresh_validates_and_archives(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    source = tmp_path / "source-events.json"
    source.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-10T12:00:00Z",
                "valid_from": "2026-08-10T00:00:00Z",
                "valid_through": "2026-08-12T00:00:00Z",
                "source": "unit-test",
                "events": [
                    {
                        "type": "macro_announcement",
                        "title": "test event",
                        "starts_at": "2026-08-10T14:00:00Z",
                        "ends_at": "2026-08-10T14:30:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = refresh_event_calendar(config, str(source))
    assert result["schema_version"] == 1
    assert config.paths.event_calendar.is_file()
    archived = list((config.paths.state_root / "audit" / "event-calendars").glob("events-*.json"))
    assert len(archived) == 1


def test_full_horizon_stack_has_bounded_compute_cadence(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    expected = {
        "5m": (5, 1),
        "15m": (15, 1),
        "30m": (30, 1),
        "60m": (60, 5),
        "120m": (120, 5),
        "EOD": (390, 5),
        "1D": (390, 15),
        "5D": (1950, 15),
    }
    assert {h.name: (h.minutes, h.cadence_minutes) for h in config.prediction.horizons} == expected
    assert next(h for h in config.prediction.horizons if h.name == "15m").trade_eligible is True


def test_replay_without_captured_matured_tape_fails_closed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    result = ReplayVerifier(config, Journal(config.paths.database)).run(samples=1)
    assert result["status"] == "FAILED"
    assert result["samples"] == 0


def test_promotion_without_evidence_remains_incomplete_and_writes_report(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.tradier.environment = "sandbox"
    config.tradier.market_environment = "production"
    config.tradier.stream_enabled = True
    config.trading.paper_mode = True
    result = PromotionEvaluator(config, Journal(config.paths.database)).run(run_replay=True, replay_samples=1)
    assert result["status"] == "PAPER_VALIDATION_INCOMPLETE"
    assert result["automatic_live_enable"] is False
    assert "paper_sessions" in result["failed_gates"]
    assert "deterministic_replay" in result["failed_gates"]
    assert config.paths.promotion_report.is_file()


def test_production_approval_accepts_only_passed_validation_report(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    evidence = tmp_path / "promotion.json"
    report = {
        "status": "PAPER_VALIDATION_INCOMPLETE",
        "automatic_live_enable": False,
        "model_version": config.prediction.model_version,
        "config_fingerprint": config.production_fingerprint(),
    }
    evidence.write_text(json.dumps(report), encoding="utf-8")
    approval = {
        "approved": True,
        "model_version": config.prediction.model_version,
        "config_fingerprint": config.production_fingerprint(),
        "evidence_path": str(evidence),
        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
    }
    config.paths.production_approval.write_text(json.dumps(approval), encoding="utf-8")
    ok, reason = config.production_approval_status()
    assert ok is False
    assert "has not passed" in reason

    report["status"] = "ELIGIBLE_FOR_MANUAL_LIVE_REVIEW"
    evidence.write_text(json.dumps(report), encoding="utf-8")
    approval["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    config.paths.production_approval.write_text(json.dumps(approval), encoding="utf-8")
    ok, reason = config.production_approval_status()
    assert ok is True
    assert reason == "approved"


def test_validation_threshold_change_invalidates_fingerprint(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    before = config.production_fingerprint()
    config.validation.minimum_profit_factor += 0.01
    assert config.production_fingerprint() != before



def test_promotable_primary_signal_requires_walk_forward_training(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.prediction.ridge_enabled is True
    assert config.prediction.ridge_min_samples >= 200
    assert config.prediction.ridge_alpha_by_horizon["15m"] == pytest.approx(1.0)
    assert config.prediction.ridge_alpha_by_horizon["30m"] == pytest.approx(0.00001)
    assert config.validation.minimum_trained_primary_fraction >= 0.90
    assert config.prediction.shadow_model_enabled is True
    assert config.validation.minimum_paper_sessions >= 60
    assert config.validation.minimum_paper_trades >= 60


def test_internal_tick_trin_and_auction_proxies_are_explicit(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)

    def quote(symbol: str, price: float, change: float, volume: float, weight: float = 0.0) -> dict:
        return {
            "symbol": symbol,
            "weight": weight,
            "sector": "Test",
            "price": price,
            "bid": price - 0.01,
            "ask": price + 0.01,
            "change_pct": change,
            "volume": volume,
            "quote_timestamp": "2026-08-10T14:01:00Z",
            "stale": False,
            "payload": {},
        }

    prior_quotes = [
        quote("SPY", 100.0, 0.0, 1_000.0),
        quote("A", 100.0, 0.0, 80.0, 0.25),
        quote("B", 100.0, 0.0, 250.0, 0.25),
        quote("C", 100.0, 0.0, 80.0, 0.25),
        quote("D", 100.0, 0.0, 80.0, 0.25),
    ]
    current_quotes = [
        quote("SPY", 101.0, 0.01, 1_500.0),
        quote("A", 101.0, 0.01, 100.0, 0.25),
        quote("B", 100.5, 0.01, 300.0, 0.25),
        quote("C", 99.0, -0.01, 100.0, 0.25),
        quote("D", 99.5, -0.01, 100.0, 0.25),
    ]
    prior = {
        "snapshot_id": "S-prior",
        "captured_at": "2026-08-10T14:00:00Z",
        "exchange_state": "open",
        "spy_price": 100.0,
        "spy_bid": 99.99,
        "spy_ask": 100.01,
        "covered_weight": 1.0,
        "quote_count": len(prior_quotes),
        "stale_quote_count": 0,
        "integrity": "VERIFIED",
        "source": "tradier_production_stream",
        "payload": {},
    }
    current = {
        **prior,
        "snapshot_id": "S-current",
        "captured_at": "2026-08-10T14:01:00Z",
        "spy_price": 101.0,
        "spy_bid": 100.99,
        "spy_ask": 101.01,
        "quote_count": len(current_quotes),
    }
    journal.insert_snapshot(prior, prior_quotes)
    journal.insert_snapshot(current, current_quotes)
    context = build_market_context(journal, config, current, current_quotes)
    signals = context.signals
    assert signals["breadth_representation"] == "SPY_constituent_internal_TICK_TRIN_proxies"
    assert signals["auction_representation"] == "captured_SPY_session_volume_profile_proxy"
    assert signals["internal_tick_normalized"] == pytest.approx(0.0)
    assert signals["advance_decline_normalized"] == pytest.approx(0.0)
    assert signals["trin_proxy"] == pytest.approx(0.5)
    assert signals["auction_vwap"] == pytest.approx(101.0)

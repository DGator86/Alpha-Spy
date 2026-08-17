from datetime import UTC, datetime, timedelta

import pytest
from test_production_hardening import _options, _position, make_config

from alpha_spy.position_management import PositionSignal, evaluate_position
from alpha_spy.session_tape import (
    blocks_bearish,
    blocks_bullish,
    blocks_short_vol,
    distance_bps,
    session_agrees_with_direction,
    structure_session_veto,
)
from alpha_spy.strategy import generate_candidates


def test_session_bias_helpers() -> None:
    assert distance_bps(773.45, 776.18) == pytest.approx(-35.17, abs=0.05)
    assert blocks_bullish(-7.7) is True
    assert blocks_bearish(-7.7) is False
    assert blocks_bearish(7.7) is True
    assert blocks_bullish(-5.0) is False
    assert blocks_short_vol(-20.0, -4.0) is True
    assert blocks_short_vol(-4.0, -4.0) is False
    assert session_agrees_with_direction(-1, -12.0) is True
    assert session_agrees_with_direction(1, -12.0) is False
    assert structure_session_veto("LONG_CALL", "directional_long", open_bps=-7.7) == "session_bias_against_calls"
    assert structure_session_veto("LONG_PUT", "directional_long", open_bps=-7.7) is None
    assert structure_session_veto("IRON_CONDOR", "short_vol", open_bps=-20.0) == "session_trend_blocks_short_vol"


def test_generate_candidates_rejects_calls_below_the_open(tmp_path) -> None:
    config = make_config(tmp_path)
    prediction = {
        "prediction_id": "P-test",
        "feature_hash": "abcd1234" + "0" * 8,
        "target_at": "2026-08-17T14:25:00Z",
        "horizon_minutes": 15,
        "spy_price": 775.56,
        "predicted_price": 775.62,
        "predicted_low": 774.5,
        "predicted_high": 776.5,
        "expected_return": 0.00008,
        "sigma_return": 0.006,
        "probability_up": 0.61,
        "payload": {
            "input_health": {"required_ok": True},
            "multi_horizon_consensus": {"aligned": True},
            "market_context": {
                "signals": {
                    "session_open_price": 776.18,
                    "session_open_distance_bps": -8.0,
                    "auction_vwap_distance_bps": -2.0,
                }
            },
        },
    }
    candidates = generate_candidates(config, prediction, _options("2026-08-17"))
    calls = [c for c in candidates if c["strategy"] in {"LONG_CALL", "CALL_DEBIT_SPREAD"}]
    puts = [c for c in candidates if c["strategy"] in {"LONG_PUT", "PUT_DEBIT_SPREAD"}]
    assert calls
    assert all(c["status"] == "REJECTED" for c in calls)
    assert all("session_bias_against_calls" in (c.get("rejection_reason") or "") for c in calls)
    assert puts  # puts remain available for scoring; they may still fail other gates


def test_put_survives_15m_horizon_while_open_unreclaimed() -> None:
    now = datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
    position = _position(now - timedelta(minutes=16))
    position["strategy"] = "LONG_PUT"
    position["payload"]["candidate"]["payload"]["family"] = "directional_long"
    signal = PositionSignal(
        forecast_return=-0.0004,
        breadth=0.40,
        iv_edge_gap=0.0,
        spot=773.50,
        session_open=776.18,
    )
    decision = evaluate_position(position, now=now, pnl=20.0, mfe=25.0, signal=signal)
    assert decision.should_exit is False
    assert decision.reason is None


def test_horizon_exit_still_fires_without_session_bias() -> None:
    now = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
    signal = PositionSignal(forecast_return=0.001, breadth=0.60, iv_edge_gap=0.0, spot=100.10)
    horizon = evaluate_position(
        _position(now - timedelta(minutes=16)), now=now, pnl=5.0, mfe=5.0, signal=signal
    )
    assert horizon.should_exit is True
    assert horizon.reason == "forecast_horizon_exit"

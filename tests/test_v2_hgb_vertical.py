from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alpha_spy.position_management import PositionSignal
from alpha_spy.v2_hgb_vertical import build_hgb_vertical_candidate
from alpha_spy.v2_settlement import evaluate_v2_position


def _option(symbol: str, right: str, strike: float, bid: float, ask: float):
    return {
        "symbol": symbol,
        "right": right,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "midpoint": 0.5 * (bid + ask),
        "open_interest": 1000,
        "volume": 100,
        "bid_size": 50,
        "ask_size": 50,
        "expiration": "2026-08-27",
        "payload": {},
    }


def _prediction(spot: float = 700.10):
    return {
        "prediction_id": "P-test",
        "spy_price": spot,
    }


def _beta(direction: str):
    bullish = direction == "BULLISH"
    return {
        "eligible": True,
        "hgb_direction": {
            "eligible": True,
            "direction": direction,
            "strength": 0.70,
            "probability_up": 0.75 if bullish else 0.25,
            "expected_return_bps": 8.0 if bullish else -8.0,
            "core_prediction_bps": 7.0 if bullish else -7.0,
            "breadth_prediction_bps": 9.0 if bullish else -9.0,
            "model_version": "beta-spy-v2-hgb-trailing-1",
        },
    }


def test_bullish_hgb_signal_builds_exact_two_point_call_vertical():
    options = [
        _option("SPY260827C00700000", "C", 700.0, 1.00, 1.02),
        _option("SPY260827C00702000", "C", 702.0, 0.44, 0.46),
        _option("SPY260827C00701000", "C", 701.0, 0.68, 0.70),
    ]
    candidate = build_hgb_vertical_candidate(_prediction(), _beta("BULLISH"), options)
    assert candidate is not None
    assert candidate["strategy"] == "CALL_DEBIT_SPREAD"
    assert candidate["width"] == 2.0
    assert [leg["strike"] for leg in candidate["legs"]] == [700.0, 702.0]
    assert candidate["max_loss"] <= 100.0
    assert candidate["payload"]["legacy_pq_ev_authority"] is False
    assert candidate["payload"]["force_horizon_exit"] is True


def test_bearish_hgb_signal_builds_exact_two_point_put_vertical():
    options = [
        _option("SPY260827P00700000", "P", 700.0, 1.00, 1.02),
        _option("SPY260827P00698000", "P", 698.0, 0.44, 0.46),
        _option("SPY260827P00699000", "P", 699.0, 0.68, 0.70),
    ]
    candidate = build_hgb_vertical_candidate(_prediction(), _beta("BEARISH"), options)
    assert candidate is not None
    assert candidate["strategy"] == "PUT_DEBIT_SPREAD"
    assert [leg["strike"] for leg in candidate["legs"]] == [700.0, 698.0]


def test_wide_or_shallow_quotes_fail_closed():
    options = [
        _option("SPY260827C00700000", "C", 700.0, 1.00, 1.08),
        _option("SPY260827C00702000", "C", 702.0, 0.44, 0.46),
    ]
    assert build_hgb_vertical_candidate(_prediction(), _beta("BULLISH"), options) is None


def _position(opened_at: datetime):
    return {
        "strategy": "CALL_DEBIT_SPREAD",
        "opened_at": opened_at.isoformat(),
        "entry_value": 0.55,
        "quantity": 1,
        "max_loss": 55.0,
        "max_profit": 145.0,
        "legs": [],
        "payload": {
            "entry_kind": "debit",
            "candidate": {
                "payload": {
                    "authority": "beta_v2_hgb_blocked_walk_forward",
                    "forecast_horizon_minutes": 15,
                    "force_horizon_exit": True,
                }
            },
        },
    }


def test_hgb_vertical_holds_until_exact_fifteen_minute_horizon():
    opened = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
    signal = PositionSignal(forecast_return=-0.01, breadth=0.10, iv_edge_gap=-0.50, spot=690.0)
    before = evaluate_v2_position(
        _position(opened), now=opened + timedelta(minutes=14, seconds=59), pnl=-40.0, mfe=0.0, signal=signal
    )
    assert before.should_exit is False
    at_horizon = evaluate_v2_position(
        _position(opened), now=opened + timedelta(minutes=15), pnl=-40.0, mfe=0.0, signal=signal
    )
    assert at_horizon.should_exit is True
    assert at_horizon.reason == "forecast_horizon_exit"

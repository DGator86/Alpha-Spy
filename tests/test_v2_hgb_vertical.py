from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alpha_spy.position_management import PositionSignal
from alpha_spy.v2_hgb_vertical import build_hgb_vertical_candidate
from alpha_spy.v2_settlement import evaluate_v2_position


def _option(symbol: str, right: str, strike: float, bid: float, ask: float):
    # Approximate same-day deltas only for deterministic unit geometry. Production
    # candidates use greeks from the captured Tradier chain and fail closed when
    # delta is missing.
    if right == "C":
        delta = {700.0: 0.52, 701.0: 0.40, 702.0: 0.30}.get(strike, 0.20)
    else:
        delta = {700.0: -0.48, 699.0: -0.38, 698.0: -0.28}.get(strike, -0.20)
    return {
        "symbol": symbol,
        "right": right,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "midpoint": 0.5 * (bid + ask),
        "delta": delta,
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


def _beta(direction: str, *, expected_return_bps: float = 8.0):
    bullish = direction == "BULLISH"
    signed_return = abs(expected_return_bps) if bullish else -abs(expected_return_bps)
    return {
        "eligible": True,
        "hgb_direction": {
            "eligible": True,
            "direction": direction,
            "strength": 0.70,
            "probability_up": 0.75 if bullish else 0.25,
            "expected_return_bps": signed_return,
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
    economics = candidate["payload"]["signal_economics"]
    assert economics["net_delta"] > 0.0
    assert economics["first_order_signal_edge_dollars"] >= economics["required_signal_edge_dollars"]


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


def test_statistical_direction_without_economic_option_edge_fails_closed():
    options = [
        _option("SPY260827C00700000", "C", 700.0, 1.00, 1.02),
        _option("SPY260827C00702000", "C", 702.0, 0.44, 0.46),
    ]
    assert (
        build_hgb_vertical_candidate(
            _prediction(),
            _beta("BULLISH", expected_return_bps=1.0),
            options,
        )
        is None
    )


def test_missing_delta_fails_closed_even_with_strong_direction_signal():
    options = [
        _option("SPY260827C00700000", "C", 700.0, 1.00, 1.02),
        _option("SPY260827C00702000", "C", 702.0, 0.44, 0.46),
    ]
    options[0]["delta"] = None
    assert build_hgb_vertical_candidate(_prediction(), _beta("BULLISH"), options) is None


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

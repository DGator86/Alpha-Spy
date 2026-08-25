"""Situation machine vs the live and perfect books (Aug 18–24)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alpha_spy.position_management import PositionSignal, evaluate_position
from alpha_spy.situation import (
    classify_situation,
    debit_premium_ok,
    structure_situation_veto,
    swing_from_path,
    trail_ready,
)
from alpha_spy.strategy import generate_candidates
from tests.test_friday_fail_safes import _chain, _debit_vertical, make_config


def test_monday_chop_is_range_not_impulse() -> None:
    # Sub-$1.40 legs between $0.75 pivots; envelope stays inside $3.
    path = [
        763.75,
        764.60,
        763.80,
        764.55,
        763.70,
        764.50,
        763.65,
        764.40,
        763.55,
        764.35,
        763.50,
        764.20,
        763.40,
    ]
    swing = swing_from_path(path)
    assert swing.confirmed is False
    waiting = classify_situation(
        minutes_open=46,
        session_range=max(path) - min(path),
        confirmed_impulse=swing.confirmed,
    )
    assert waiting == "WATCH"
    situation = classify_situation(
        minutes_open=91,
        session_range=max(path) - min(path),
        confirmed_impulse=swing.confirmed,
    )
    assert situation == "RANGE"
    assert structure_situation_veto("LONG_STRANGLE", "long_vol", situation=situation) == (
        "long_vol_disabled_tape_policy"
    )
    assert structure_situation_veto(
        "PUT_DEBIT_SPREAD", "directional_long", situation=situation
    ) == "range_day_condor_only"
    assert structure_situation_veto("IRON_CONDOR", "short_vol", situation=situation) is None


def test_impulse_leg_selects_matching_debit() -> None:
    path = [770.25, 769.80, 769.20, 768.73]
    swing = swing_from_path(path)
    assert swing.confirmed is True
    assert swing.direction == -1
    situation = classify_situation(
        minutes_open=28,
        session_range=1.52,
        confirmed_impulse=True,
    )
    assert situation == "IMPULSE"
    assert (
        structure_situation_veto(
            "PUT_DEBIT_SPREAD", "directional_long", situation=situation, direction=-1
        )
        is None
    )
    assert structure_situation_veto(
        "CALL_DEBIT_SPREAD", "directional_long", situation=situation, direction=-1
    ) == "impulse_direction_mismatch"
    assert structure_situation_veto("IRON_CONDOR", "short_vol", situation=situation) == (
        "impulse_blocks_condor"
    )


def test_junk_debits_from_monday_are_rejected() -> None:
    assert debit_premium_ok(0.05, 3.0) is False
    assert debit_premium_ok(0.18, 3.0) is False
    assert debit_premium_ok(0.27, 3.0) is True
    assert debit_premium_ok(0.56, 3.0) is True
    assert debit_premium_ok(0.81, 2.0) is True


def test_defined_debit_does_not_trail_a_third_of_the_debit() -> None:
    opened = datetime(2026, 8, 19, 14, 51, tzinfo=UTC)
    now = opened + timedelta(minutes=12)
    signal = PositionSignal(
        forecast_return=0.001,
        breadth=0.62,
        iv_edge_gap=0.0,
        spot=771.80,
        session_open=770.36,
    )
    position = _debit_vertical(opened, strategy="CALL_DEBIT_SPREAD")
    decision = evaluate_position(position, now=now, pnl=26.0, mfe=32.0, signal=signal)
    assert decision.should_exit is False
    assert trail_ready(32.0, 210.0) is False
    assert trail_ready(110.0, 210.0) is True


def test_range_payload_rejects_directionals_and_keeps_condor(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.strategy.min_probability = 0.0
    config.strategy.min_edge_dollars = -1000.0
    config.strategy.min_edge_to_uncertainty = 0.0
    config.strategy.require_positive_doubled_cost_ev = False
    config.strategy.require_multi_horizon_alignment = False
    config.risk.maximum_trade_risk_dollars = 100_000.0
    prediction = {
        "prediction_id": "P-RANGE",
        "feature_hash": "12345678" + "0" * 56,
        "spy_price": 100.0,
        "predicted_price": 102.0,
        "predicted_low": 97.0,
        "predicted_high": 103.0,
        "expected_return": 0.02,
        "sigma_return": 0.01,
        "probability_up": 0.72,
        "payload": {
            "input_health": {"required_ok": True},
            "market_context": {
                "signals": {
                    "situation": "RANGE",
                    "situation_direction": 0,
                    "session_range": 1.78,
                }
            },
        },
    }
    candidates = generate_candidates(config, prediction, _chain())
    directionals = [
        row
        for row in candidates
        if row["strategy"] in {"CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD", "LONG_PUT", "LONG_STRANGLE"}
    ]
    condors = [row for row in candidates if row["strategy"] == "IRON_CONDOR"]
    assert directionals
    assert all(row["status"] == "REJECTED" for row in directionals)
    assert condors
    assert any("range_day_condor_only" in str(row["rejection_reason"]) for row in directionals)
    assert all("range_day_condor_only" not in str(row.get("rejection_reason") or "") for row in condors)


def test_dollar_forty_needs_eight_minutes_to_be_tradeable() -> None:
    short = swing_from_path([770.25, 769.80, 769.20, 768.73])
    assert short.confirmed is True
    assert short.tradeable is False
    prices = [770.0]
    for i in range(1, 9):
        prices.append(770.0 - 0.20 * i)
    long_leg = swing_from_path(prices)
    assert long_leg.confirmed is True
    assert long_leg.tradeable is True
    assert long_leg.had_tradeable is True
    assert long_leg.direction == -1


def test_range_is_blocked_after_a_tradeable_impulse() -> None:
    situation = classify_situation(
        minutes_open=46,
        session_range=1.78,
        confirmed_impulse=False,
        had_tradeable_impulse=True,
    )
    assert situation == "TREND"
    assert structure_situation_veto("IRON_CONDOR", "short_vol", situation=situation) == (
        "trend_day_blocks_condor"
    )
    assert (
        structure_situation_veto(
            "PUT_DEBIT_SPREAD", "directional_long", situation=situation, direction=-1
        )
        is None
    )


def test_reverse_off_a_tradeable_leg_stays_impulse() -> None:
    prices = [770.0]
    for i in range(1, 9):
        prices.append(770.0 - 0.20 * i)
    prices.append(768.40 + 0.80)
    swing = swing_from_path(prices)
    assert swing.reversed_from_tradeable is True
    assert swing.pending_direction == 1
    assert swing.direction == 1
    assert swing.had_tradeable is True
    assert swing.last_tradeable_direction == -1
    situation = classify_situation(
        minutes_open=40,
        session_range=1.60,
        confirmed_impulse=bool(swing.tradeable or swing.reversed_from_tradeable),
        had_tradeable_impulse=swing.had_tradeable,
    )
    assert situation == "IMPULSE"

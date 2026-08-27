from datetime import UTC, datetime

from alpha_spy.v2_trader_agent import build_agent_plan


def _candidate(strategy: str, *, ev: float = 20.0, drag: float = 2.0, pop: float = 0.65):
    return {
        "candidate_id": f"C-{strategy}",
        "strategy": strategy,
        "status": "ELIGIBLE",
        "expected_value": ev,
        "probability_profit": pop,
        "max_loss": 50.0,
        "max_profit": 100.0,
        "entry_price": 0.50,
        "entry_kind": "debit",
        "legs": [{"symbol": "SPYOPT", "side": "buy_to_open", "quantity": 1}],
        "payload": {"v2": {"estimated_execution_drag_dollars": drag, "combined_spread": 0.02}},
    }


def _beta(regime: str = "DIRECTIONAL_UP"):
    return {
        "regime_forecast": {
            "definable": True,
            "current_regime": regime,
            "confidence": 0.80,
            "persistence_15": 0.70,
            "persistence_30": 0.60,
            "expected_duration_minutes": 22.0,
            "successor_probabilities": {
                "QUIET": 0.10,
                "DIRECTIONAL_UP": 0.55,
                "DIRECTIONAL_DOWN": 0.05,
                "EXPANSION": 0.20,
                "TRANSITION": 0.10,
            },
            "most_likely_successor": "EXPANSION",
            "successor_confidence": 0.20,
        },
        "predictive_state": {
            "ready": True,
            "p_big_15": 0.45,
            "p_reversal_15": 0.15,
        },
        "hgb_direction": {"eligible": True, "direction": "BULLISH", "strength": 0.60},
    }


def test_undefined_regime_never_trades():
    beta = _beta()
    beta["regime_forecast"]["definable"] = False
    plan = build_agent_plan(beta, [_candidate("BULL_CALL_DEBIT_SPREAD")], now=datetime(2026, 8, 27, 15, 0, tzinfo=UTC))
    assert plan.action == "NO_TRADE"
    assert plan.reason == "regime_not_definable"


def test_directional_thesis_records_full_chain():
    plan = build_agent_plan(
        _beta(),
        [_candidate("BULL_CALL_DEBIT_SPREAD")],
        now=datetime(2026, 8, 27, 15, 0, tzinfo=UTC),
    )
    assert plan.action == "ENTER"
    assert plan.playbook == "DIRECTIONAL_MOMENTUM"
    assert plan.thesis is not None
    assert plan.thesis.regime == "DIRECTIONAL_UP"
    assert plan.thesis.expected_time_to_profit_minutes == 15.0
    assert plan.thesis.first_profit_target_dollars > 0
    assert plan.thesis.stop_loss_dollars > 0
    assert plan.thesis.economics["robust_ev_after_3x_drag_dollars"] > 0


def test_range_playbook_waits_until_late_session_then_can_enter():
    beta = _beta("QUIET")
    beta["hgb_direction"] = {"eligible": False}
    beta["predictive_state"]["p_big_15"] = 0.20
    beta["regime_forecast"]["persistence_30"] = 0.90
    beta["regime_forecast"]["most_likely_successor"] = "QUIET"
    butterfly = _candidate("IRON_BUTTERFLY", ev=24.0, drag=2.0, pop=0.70)

    early = build_agent_plan(beta, [butterfly], now=datetime(2026, 8, 27, 18, 30, tzinfo=UTC))
    assert early.action == "WAIT"
    assert early.playbook == "LATE_RANGE_CARRY"

    late = build_agent_plan(beta, [butterfly], now=datetime(2026, 8, 27, 19, 5, tzinfo=UTC))
    assert late.action == "ENTER"
    assert late.thesis is not None
    assert late.thesis.expected_time_to_profit_minutes == 30.0


def test_transition_edge_waits_for_the_transition_instead_of_front_running():
    beta = _beta("TRANSITION")
    beta["hgb_direction"] = {"eligible": False}
    beta["regime_forecast"]["most_likely_successor"] = "DIRECTIONAL_DOWN"
    beta["regime_forecast"]["successor_confidence"] = 0.55
    put = _candidate("BEAR_PUT_DEBIT_SPREAD")
    plan = build_agent_plan(beta, [put], now=datetime(2026, 8, 27, 16, 0, tzinfo=UTC))
    assert plan.action == "WAIT"
    assert plan.playbook == "REGIME_TRANSITION"
    assert plan.thesis is not None
    assert plan.thesis.entry_mode == "WAIT_FOR_TRANSITION"

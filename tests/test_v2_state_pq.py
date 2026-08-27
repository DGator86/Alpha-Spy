from copy import deepcopy

import pytest

from alpha_spy.v2_state_pq import (
    attach_empirical_state_p,
    authorize_state_pq_challenger,
    find_primary_valuation,
)


def _beta_state(*, hgb_eligible: bool = True):
    outcomes = [float(value) for value in range(-15, 15)]
    return {
        "hgb_direction": {
            "eligible": hgb_eligible,
            "expected_return_bps": 6.0,
        },
        "predictive_state": {
            "ready": True,
            "regime": "DIRECTIONAL_UP" if hgb_eligible else "TRANSITION",
            "analog_y15_bps": outcomes,
            "analog_weights": [1.0 / len(outcomes)] * len(outcomes),
            "analog_count": len(outcomes),
            "effective_analogs": 30.0,
            "mean_proximity": 0.35,
            "conformal_scale": 1.10,
            "direct_pred_15": 2.0,
            "sigma_15": 8.0,
            "p_big_15": 0.45,
            "p_persistent_30": 0.55,
            "p_reversal_15": 0.25,
            "p_reversal_30": 0.30,
            "p_acceleration": 0.40,
            "model_version": "test-state",
        },
    }


def test_empirical_state_replaces_only_p_and_preserves_q():
    prediction = {
        "spy_price": 700.0,
        "expected_return": 0.0,
        "sigma_return": 0.001,
        "payload": {
            "distribution": {
                "probability_grid": [0.1, 0.25, 0.5, 0.75, 0.9],
                "p_price_quantiles": [699.0, 699.5, 700.0, 700.5, 701.0],
                "q_price_quantiles": [698.8, 699.4, 700.0, 700.6, 701.2],
                "q_volatility": 0.0015,
                "q_source": "live-option-surface",
            }
        },
    }
    q_before = deepcopy(prediction["payload"]["distribution"]["q_price_quantiles"])

    out = attach_empirical_state_p(prediction, _beta_state())
    distribution = out["payload"]["distribution"]

    assert distribution["q_price_quantiles"] == q_before
    assert distribution["q_source"] == "live-option-surface"
    assert distribution["p_source"] == "beta_predictive_state_empirical_analogs"
    assert distribution["p_price_quantiles"] != prediction["payload"]["distribution"][
        "p_price_quantiles"
    ]
    assert out["expected_return"] == pytest.approx(0.0006, abs=2e-4)
    assert out["payload"]["state_pq"]["q_unchanged"] is True


def _candidate(candidate_id, strategy, legs, *, ev, score, pop, drag, pnl_std=20.0):
    return {
        "candidate_id": candidate_id,
        "strategy": strategy,
        "status": "ELIGIBLE",
        "expected_value": ev,
        "score": score,
        "probability_profit": pop,
        "max_loss": 80.0,
        "legs": legs,
        "payload": {
            "p_pnl_std": pnl_std,
            "v2": {"estimated_execution_drag_dollars": drag},
        },
    }


def test_challenger_must_beat_exact_incumbent_under_execution_stress():
    primary_legs = [
        {"symbol": "C700", "side": "buy_to_open", "quantity": 1},
        {"symbol": "C702", "side": "sell_to_open", "quantity": 1},
    ]
    primary = {"candidate_id": "primary", "legs": primary_legs}
    incumbent = _candidate(
        "incumbent-value",
        "BULL_CALL_DEBIT_SPREAD",
        primary_legs,
        ev=10.0,
        score=0.20,
        pop=0.60,
        drag=1.0,
    )
    challenger = _candidate(
        "challenger",
        "CALL_BACKSPREAD_1x2",
        [
            {"symbol": "C700", "side": "sell_to_open", "quantity": 1},
            {"symbol": "C702", "side": "buy_to_open", "quantity": 2},
        ],
        ev=25.0,
        score=0.32,
        pop=0.62,
        drag=2.0,
    )
    candidates = [incumbent, challenger]

    valuation = find_primary_valuation(primary, candidates)
    chosen, detail = authorize_state_pq_challenger(
        primary,
        valuation,
        candidates,
        _beta_state(),
        [{"iv": 0.20, "strike": 700.0}],
    )

    assert valuation is incumbent
    assert chosen is challenger
    assert detail["challenger_authorized"] is True
    assert detail["challenger_family"] == "CALL_BACKSPREAD_1x2"

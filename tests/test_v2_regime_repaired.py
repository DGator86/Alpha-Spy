import numpy as np

from alpha_spy.regime import RegimeState
from alpha_spy.v2_regime_repaired import (
    _horizon_ewma,
    hierarchy_conflict_score,
    tie_aware_percentile,
)


def _state(
    *,
    volatility="normal",
    correlation="stable",
    breadth="mixed",
    concentration="distributed",
):
    return RegimeState(
        volatility=volatility,
        correlation=correlation,
        breadth=breadth,
        concentration=concentration,
        dealer_gamma="positive_gamma",
        session="midday",
        event="ordinary",
        transition_risk=False,
        history_samples=100,
        risk_tone="neutral",
        volatility_term="contango",
        liquidity="normal",
    )


def test_tie_aware_percentile_keeps_tied_floor_at_midrank():
    history = np.full(240, 0.00045)
    assert tie_aware_percentile(history, 0.00045) == 0.5


def test_horizon_ewma_reacts_more_quickly_at_micro_horizon():
    # Historical tape was broad-up, while the current observation and newest
    # twenty bars are a sharp breadth break. Micro should react much more than
    # structural state without using any future observations.
    history = np.asarray([0.15] * 20 + [0.85] * 500, dtype=float)
    micro = _horizon_ewma(0.15, history, 45)
    structural = _horizon_ewma(0.15, history, 1950)
    assert micro < structural
    assert structural - micro > 0.20


def test_identical_hierarchy_has_zero_conflict():
    state = _state()
    levels = dict.fromkeys(("micro", "intraday", "swing", "structural"), state)
    assert hierarchy_conflict_score(levels) == 0.0


def test_cross_horizon_break_produces_actionable_conflict():
    levels = {
        "micro": _state(
            volatility="crisis",
            correlation="dislocated",
            breadth="broad_down",
            concentration="concentrated",
        ),
        "intraday": _state(
            volatility="high",
            correlation="rising",
            breadth="broad_down",
            concentration="concentrated",
        ),
        "swing": _state(
            volatility="normal",
            correlation="stable",
            breadth="broad_up",
            concentration="distributed",
        ),
        "structural": _state(
            volatility="low",
            correlation="falling",
            breadth="broad_up",
            concentration="distributed",
        ),
    }
    assert hierarchy_conflict_score(levels) >= 0.65

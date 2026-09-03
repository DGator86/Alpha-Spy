from __future__ import annotations

import pytest

from spy_platform.contracts import AlphaState, BetaState, GammaState, ModelMeta
from spy_platform.model_bus import ModelStateBus


def _alpha(timestamp="2026-09-03T14:42:00Z"):
    return AlphaState(
        meta=ModelMeta.create(
            model="ALPHA",
            timestamp=timestamp,
            model_version="a",
            data_quality=1.0,
        ),
        probability_up={15: 0.65},
    )


def _beta(timestamp="2026-09-03T14:42:10Z"):
    return BetaState(
        meta=ModelMeta.create(
            model="BETA",
            timestamp=timestamp,
            model_version="b",
            data_quality=1.0,
        ),
        probability_up={15: 0.55},
    )


def _gamma(timestamp="2026-09-03T14:42:20Z"):
    return GammaState(
        meta=ModelMeta.create(
            model="GAMMA",
            timestamp=timestamp,
            model_version="g",
            data_quality=1.0,
        )
    )


def test_bus_requires_all_independent_models():
    bus = ModelStateBus()
    bus.publish_alpha(_alpha())
    assert bus.status().ready is False
    with pytest.raises(RuntimeError):
        bus.compile()


def test_bus_compiles_synchronized_states_without_blending_sources():
    bus = ModelStateBus(max_clock_skew_seconds=30)
    alpha = _alpha()
    beta = _beta()
    gamma = _gamma()
    bus.publish_alpha(alpha)
    bus.publish_beta(beta)
    bus.publish_gamma(gamma)
    delta = bus.compile()
    assert delta.alpha is alpha
    assert delta.beta is beta
    assert delta.gamma is gamma
    assert bus.status().reason == "synchronized"


def test_bus_refuses_stale_cross_model_convergence():
    bus = ModelStateBus(max_clock_skew_seconds=30)
    bus.publish_alpha(_alpha("2026-09-03T14:42:00Z"))
    bus.publish_beta(_beta("2026-09-03T14:42:05Z"))
    bus.publish_gamma(_gamma("2026-09-03T14:44:00Z"))
    status = bus.status()
    assert status.ready is False
    assert status.reason.startswith("clock_skew:")
    with pytest.raises(RuntimeError):
        bus.compile()

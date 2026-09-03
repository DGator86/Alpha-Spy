from __future__ import annotations

from spy_platform.contracts import AlphaState, BetaState, ModelMeta
from spy_platform.delta import compile_delta_state
from spy_platform.gamma import build_gamma_state
from spy_platform.streams import build_delta_streams


def _alpha(probability: float) -> AlphaState:
    return AlphaState(
        meta=ModelMeta.create(
            model="ALPHA",
            timestamp="2026-09-03T14:42:00Z",
            model_version="alpha-test",
            data_quality=0.99,
        ),
        probability_up={15: probability},
    )


def _beta(probability: float) -> BetaState:
    return BetaState(
        meta=ModelMeta.create(
            model="BETA",
            timestamp="2026-09-03T14:42:00Z",
            model_version="beta-test",
            data_quality=0.98,
        ),
        probability_up={15: probability},
    )


def _gamma():
    rows = [
        {
            "right": "call",
            "strike": 652.0,
            "iv": 0.18,
            "delta": 0.55,
            "gamma": 0.04,
            "open_interest": 1000,
            "volume": 500,
            "bid": 1.20,
            "ask": 1.24,
        },
        {
            "right": "put",
            "strike": 652.0,
            "iv": 0.19,
            "delta": -0.45,
            "gamma": 0.04,
            "open_interest": 1200,
            "volume": 450,
            "bid": 1.10,
            "ask": 1.14,
        },
        {
            "right": "call",
            "strike": 655.0,
            "iv": 0.17,
            "delta": 0.25,
            "gamma": 0.02,
            "open_interest": 800,
            "volume": 300,
            "bid": 0.55,
            "ask": 0.58,
        },
        {
            "right": "put",
            "strike": 649.0,
            "iv": 0.21,
            "delta": -0.25,
            "gamma": 0.02,
            "open_interest": 900,
            "volume": 350,
            "bid": 0.60,
            "ask": 0.64,
        },
    ]
    return build_gamma_state(
        timestamp="2026-09-03T14:42:00Z",
        spot=652.25,
        chains=[{"expiration": "2026-09-03", "options": rows}],
    )


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _all_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _all_keys(child)


def test_delta_preserves_model_divergence():
    delta = compile_delta_state(_alpha(0.80), _beta(0.30), _gamma())
    h15 = delta.convergence["horizons"][15]
    assert h15["scores"]["alpha"] > 0
    assert h15["scores"]["beta"] < 0
    assert "DIRECTION_SIGN_CONFLICT_15M" in delta.conflicts
    assert h15["directional_divergence"] > 0


def test_gamma_is_measurement_only_and_does_not_fake_signed_flow():
    gamma = _gamma()
    assert gamma.directional_score is None
    assert gamma.positioning["gamma"]["dealer_inventory_observed"] is False
    assert gamma.activity["aggressor_side_observed"] is False
    assert gamma.activity["interpretation"] == "unsigned_chain_activity_not_order_flow"


def test_delta_streams_cannot_emit_trade_or_broker_instructions():
    streams = build_delta_streams(compile_delta_state(_alpha(0.65), _beta(0.62), _gamma()))
    keys = set(_all_keys(streams))
    forbidden = {
        "trade",
        "order",
        "broker",
        "position_size",
        "quantity",
        "strike_selection",
        "strategy_selection",
    }
    assert keys.isdisjoint(forbidden)


def test_delta_quality_is_component_visible():
    delta = compile_delta_state(_alpha(0.55), _beta(0.56), _gamma())
    quality = delta.data_quality
    assert quality["models"]["alpha"] == 0.99
    assert quality["models"]["beta"] == 0.98
    assert "gamma" in quality["models"]
    assert quality["composite"] <= 1.0

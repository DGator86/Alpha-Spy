from datetime import UTC, datetime, timedelta

from alpha_spy.beta_v2 import BetaV2State, attach_beta_v2_state
from alpha_spy.liquidity_v2 import liquid_option_pool, option_liquidity


def _option(symbol: str, strike: float, bid: float, ask: float, *, oi: int = 1000, volume: int = 100):
    return {
        "symbol": symbol,
        "strike": strike,
        "right": "C",
        "bid": bid,
        "ask": ask,
        "midpoint": (bid + ask) / 2,
        "bid_size": 100,
        "ask_size": 100,
        "open_interest": oi,
        "volume": volume,
    }


def test_liquidity_score_penalizes_wide_market() -> None:
    tight = option_liquidity(_option("TIGHT", 700.0, 0.99, 1.00))
    wide = option_liquidity(_option("WIDE", 700.0, 0.70, 1.00))
    assert tight is not None and wide is not None
    assert tight.score > wide.score
    assert tight.relative_spread < wide.relative_spread


def test_liquid_pool_rejects_deceptively_cheap_wide_option() -> None:
    options = [
        _option("PENNY", 700.0, 0.49, 0.50),
        _option("CHEAP_WIDE", 701.0, 0.01, 0.05),
        {**_option("PUT_PENNY", 700.0, 0.48, 0.49), "right": "P"},
    ]
    pool = liquid_option_pool(options, spot=700.0, max_spread_dollars=0.05, max_relative_spread=0.25)
    symbols = {row["symbol"] for row in pool}
    assert "PENNY" in symbols
    assert "PUT_PENNY" in symbols
    assert "CHEAP_WIDE" not in symbols


def test_beta_v2_context_never_has_strategy_authority() -> None:
    now = datetime.now(UTC)
    state = BetaV2State(
        timestamp=now,
        regime="DIRECTIONAL_EXPANSION",
        probability_big_move=0.72,
        probability_up_given_big_move=0.61,
        expected_abs_move_bps=12.0,
        validated_direction_edge=0.22,
        magnitude_trust=0.5,
        direction_trust=0.4,
        overall_trust=0.45,
        version="beta-spy-v2.0",
    )
    prediction = attach_beta_v2_state({"payload": {}}, state)
    beta = prediction["payload"]["beta_v2"]
    assert beta["strategy_authority"] is False
    assert "strategy" not in beta


def test_beta_v2_freshness_is_fail_closed() -> None:
    now = datetime.now(UTC)
    state = BetaV2State(
        timestamp=now - timedelta(minutes=10),
        regime="NORMAL",
        probability_big_move=0.5,
        probability_up_given_big_move=0.5,
        expected_abs_move_bps=4.0,
        validated_direction_edge=0.0,
        magnitude_trust=0.2,
        direction_trust=0.1,
        overall_trust=0.14,
        version="beta-spy-v2.0",
    )
    assert state.is_current(now) is False

from alpha_spy.v2_trade_management import (
    BAIL,
    HOLD,
    SCALE,
    SELL_FOR_LOSS,
    TAKE_PROFIT,
    manage_trade,
)


def _thesis(playbook: str = "DIRECTIONAL_MOMENTUM"):
    return {
        "playbook": playbook,
        "direction": "BULLISH",
        "regime": "DIRECTIONAL_UP" if playbook == "DIRECTIONAL_MOMENTUM" else "QUIET",
        "most_likely_successor": "DIRECTIONAL_UP" if playbook == "DIRECTIONAL_MOMENTUM" else "QUIET",
        "first_profit_target_dollars": 6.0,
        "second_profit_target_dollars": 18.0,
        "stop_loss_dollars": 12.0,
        "expected_time_to_profit_minutes": 15.0 if playbook == "DIRECTIONAL_MOMENTUM" else 30.0,
        "time_stop_minutes": 22.0 if playbook == "DIRECTIONAL_MOMENTUM" else 40.0,
    }


def _beta(regime: str = "DIRECTIONAL_UP"):
    return {
        "regime_forecast": {
            "definable": True,
            "current_regime": regime,
            "confidence": 0.80,
            "persistence_15": 0.70,
            "most_likely_successor": regime,
            "successor_probabilities": {
                "DIRECTIONAL_UP": 0.65,
                "DIRECTIONAL_DOWN": 0.05,
                "QUIET": 0.15,
                "EXPANSION": 0.10,
                "TRANSITION": 0.05,
            },
        },
        "predictive_state": {"p_big_15": 0.30},
        "hgb_direction": {"eligible": True, "direction": "BULLISH"},
    }


def test_second_target_takes_profit():
    result = manage_trade(
        _thesis(),
        elapsed_minutes=10,
        fair_pnl=20,
        liquidation_pnl=17,
        mfe=20,
        quantity=1,
        beta=_beta(),
        current_iv=0.20,
        entry_iv=0.20,
    )
    assert result.action == TAKE_PROFIT
    assert result.should_exit is True


def test_first_target_scales_multi_unit_position():
    result = manage_trade(
        _thesis(),
        elapsed_minutes=8,
        fair_pnl=8,
        liquidation_pnl=5,
        mfe=8,
        quantity=2,
        beta=_beta(),
        current_iv=0.20,
        entry_iv=0.20,
    )
    assert result.action == SCALE
    assert result.scale_quantity == 1
    assert result.should_exit is False


def test_directional_flip_bails_immediately():
    beta = _beta("DIRECTIONAL_DOWN")
    beta["hgb_direction"] = {"eligible": True, "direction": "BEARISH"}
    beta["regime_forecast"]["successor_probabilities"]["DIRECTIONAL_DOWN"] = 0.60
    result = manage_trade(
        _thesis(),
        elapsed_minutes=6,
        fair_pnl=2,
        liquidation_pnl=-1,
        mfe=4,
        quantity=1,
        beta=beta,
        current_iv=0.20,
        entry_iv=0.20,
    )
    assert result.action == BAIL
    assert result.should_exit is True


def test_range_iv_shock_invalidates_short_vol_even_before_price_stop():
    beta = _beta("QUIET")
    beta["hgb_direction"] = {"eligible": False}
    result = manage_trade(
        _thesis("LATE_RANGE_CARRY"),
        elapsed_minutes=12,
        fair_pnl=-3,
        liquidation_pnl=-8,
        mfe=1,
        quantity=1,
        beta=beta,
        current_iv=0.26,
        entry_iv=0.20,
    )
    assert result.action == SELL_FOR_LOSS
    assert result.should_exit is True
    assert result.reason == "implied_volatility_expanded_five_points"


def test_expected_time_failure_exits_instead_of_hoping():
    result = manage_trade(
        _thesis(),
        elapsed_minutes=16,
        fair_pnl=-2,
        liquidation_pnl=-4,
        mfe=3,
        quantity=1,
        beta=_beta(),
        current_iv=0.20,
        entry_iv=0.20,
    )
    assert result.action == SELL_FOR_LOSS
    assert result.reason == "trade_failed_on_expected_time_to_profit"


def test_valid_trade_holds_when_on_schedule():
    result = manage_trade(
        _thesis(),
        elapsed_minutes=7,
        fair_pnl=3,
        liquidation_pnl=1,
        mfe=4,
        quantity=1,
        beta=_beta(),
        current_iv=0.20,
        entry_iv=0.20,
    )
    assert result.action == HOLD
    assert result.should_exit is False

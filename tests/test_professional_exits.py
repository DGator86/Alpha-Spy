from types import SimpleNamespace

import numpy as np

from alpha_spy.research.backtest.professional_exits import SignalState, professional_liquidation

MINUTES_PER_YEAR = 252 * 390


def _candidate(**overrides):
    data = {
        "name": "LONG_CALL",
        "family": "directional_long",
        "legs": [("C", 100.0, 1)],
        "entry_cashflow": 1.0,
        "max_loss": 1.0,
        "max_profit": float("inf"),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _liquidation(candidate, *, spot, tenor, market_iv, market_skew):
    del tenor, market_iv, market_skew
    if candidate.entry_cashflow > 0:
        return 1.0 + 0.65 * max(spot - 100.0, 0.0)
    return candidate.entry_cashflow + 0.05


def test_long_call_uses_profit_trail_without_future_leakage():
    prices = np.full(390, 100.0)
    prices[101:106] = [100.4, 100.8, 101.4, 101.8, 101.2]
    prices[106:] = 101.0
    iv = np.full(390, 0.20)
    result = professional_liquidation(
        _candidate(),
        entry_minute=100,
        default_exit_minute=385,
        entry_spot=100.0,
        entry_forecast_return=0.004,
        entry_breadth=0.60,
        entry_model_p_iv=0.20,
        minutes_per_year=MINUTES_PER_YEAR,
        spy_prices=prices,
        market_iv=iv,
        market_skew=np.full(390, 0.20),
        model_q_iv=np.full(390, 0.205),
        signal_at=lambda _: SignalState(0.003, 0.60, 0.35),
        liquidation_cashflow=_liquidation,
    )
    assert result.reason == "directional_profit_trail"
    assert 103 <= result.minute <= 106
    assert result.mfe > 0.0
    assert result.minute < 385


def test_credit_spread_exits_before_short_strike_breach():
    candidate = _candidate(
        name="BULL_PUT_CREDIT_SPREAD",
        family="directional_credit",
        legs=[("P", 98.0, 1), ("P", 100.0, -1)],
        entry_cashflow=-0.30,
        max_loss=1.70,
        max_profit=0.30,
    )
    prices = np.full(390, 102.0)
    prices[101:105] = [101.0, 100.7, 100.45, 100.35]
    iv = np.full(390, 0.22)

    def credit_mark(candidate, *, spot, tenor, market_iv, market_skew):
        del tenor, market_iv, market_skew
        # Increasing liability as the short put is approached.
        liability = 0.27 + max(0.0, 101.0 - spot) * 0.50
        return -liability

    result = professional_liquidation(
        candidate,
        entry_minute=100,
        default_exit_minute=385,
        entry_spot=102.0,
        entry_forecast_return=0.003,
        entry_breadth=0.58,
        entry_model_p_iv=0.20,
        minutes_per_year=MINUTES_PER_YEAR,
        spy_prices=prices,
        market_iv=iv,
        market_skew=np.full(390, 0.20),
        model_q_iv=np.full(390, 0.20),
        signal_at=lambda _: SignalState(0.002, 0.56, 0.35),
        liquidation_cashflow=credit_mark,
    )
    assert result.reason in {"pre_strike_tail_exit", "credit_tail_stop"}
    assert result.spot > 100.0
    assert result.minute < 385


def test_scheduled_exit_is_never_exceeded():
    prices = np.full(390, 100.2)
    iv = np.full(390, 0.20)
    result = professional_liquidation(
        _candidate(),
        entry_minute=370,
        default_exit_minute=385,
        entry_spot=100.2,
        entry_forecast_return=0.002,
        entry_breadth=0.58,
        entry_model_p_iv=0.20,
        minutes_per_year=MINUTES_PER_YEAR,
        spy_prices=prices,
        market_iv=iv,
        market_skew=np.full(390, 0.20),
        model_q_iv=np.full(390, 0.205),
        signal_at=lambda _: SignalState(0.002, 0.58, 0.35),
        liquidation_cashflow=_liquidation,
    )
    assert result.minute <= 385

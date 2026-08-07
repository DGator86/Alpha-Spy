from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm


def black_scholes_price(
    spot: float,
    strike: float,
    tenor: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    right: str,
) -> float:
    if tenor <= 0:
        return max(spot - strike, 0.0) if right.upper().startswith("C") else max(strike - spot, 0.0)
    if volatility <= 0:
        forward_pv = spot * math.exp(-dividend_yield * tenor) - strike * math.exp(-rate * tenor)
        return max(forward_pv, 0.0) if right.upper().startswith("C") else max(-forward_pv, 0.0)
    root_t = math.sqrt(tenor)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility**2) * tenor
    ) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    if right.upper().startswith("C"):
        return spot * math.exp(-dividend_yield * tenor) * norm.cdf(d1) - strike * math.exp(-rate * tenor) * norm.cdf(d2)
    return strike * math.exp(-rate * tenor) * norm.cdf(-d2) - spot * math.exp(-dividend_yield * tenor) * norm.cdf(-d1)


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    tenor: float,
    rate: float,
    dividend_yield: float,
    right: str,
) -> float:
    intrinsic = black_scholes_price(spot, strike, tenor, rate, dividend_yield, 1e-9, right)
    if price < intrinsic - 1e-8:
        raise ValueError("Option price is below no-arbitrage intrinsic bound")

    def objective(vol: float) -> float:
        return black_scholes_price(spot, strike, tenor, rate, dividend_yield, vol, right) - price

    try:
        return float(brentq(objective, 1e-6, 8.0, maxiter=200))
    except ValueError as exc:
        raise ValueError("Could not recover implied volatility") from exc

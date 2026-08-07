from __future__ import annotations

import math


def american_binomial_price(
    spot: float,
    strike: float,
    tenor: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    right: str,
    steps: int = 300,
) -> float:
    """Cox-Ross-Rubinstein American option price with continuous dividend yield."""
    if tenor <= 0:
        return max(spot - strike, 0.0) if right.upper().startswith("C") else max(strike - spot, 0.0)
    steps = max(10, int(steps))
    dt = tenor / steps
    u = math.exp(volatility * math.sqrt(dt))
    d = 1.0 / u
    growth = math.exp((rate - dividend_yield) * dt)
    p = (growth - d) / (u - d)
    p = min(max(p, 0.0), 1.0)
    disc = math.exp(-rate * dt)
    prices = [spot * (u ** (steps - j)) * (d**j) for j in range(steps + 1)]
    if right.upper().startswith("C"):
        values = [max(s - strike, 0.0) for s in prices]
    else:
        values = [max(strike - s, 0.0) for s in prices]
    for n in range(steps - 1, -1, -1):
        next_values = []
        for j in range(n + 1):
            s = spot * (u ** (n - j)) * (d**j)
            continuation = disc * (p * values[j] + (1.0 - p) * values[j + 1])
            exercise = max(s - strike, 0.0) if right.upper().startswith("C") else max(strike - s, 0.0)
            next_values.append(max(continuation, exercise))
        values = next_values
    return float(values[0])

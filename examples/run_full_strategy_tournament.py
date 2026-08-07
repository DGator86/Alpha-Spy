from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spy_constituent_alpha.backtest.engine import summarize_expiration_backtest
from spy_constituent_alpha.backtest.metrics import brier_score, calibration_error, log_score

MINUTES_PER_DAY = 390
MINUTES_PER_YEAR = 252 * MINUTES_PER_DAY
RATE = 0.045
DIVIDEND_YIELD = 0.012
MULTIPLIER = 100
FEE_PER_CONTRACT = 0.65


@dataclass(frozen=True)
class Regime:
    name: str
    probability: float
    annual_vol: float
    average_correlation: float
    daily_drift: float
    jump_probability: float


REGIMES = (
    Regime("range", 0.25, 0.125, 0.24, 0.0000, 0.02),
    Regime("bull", 0.20, 0.155, 0.32, 0.0045, 0.03),
    Regime("bear", 0.18, 0.205, 0.46, -0.0060, 0.05),
    Regime("high_vol", 0.15, 0.285, 0.55, -0.0010, 0.08),
    Regime("correlation_shock", 0.12, 0.245, 0.70, -0.0035, 0.07),
    Regime("jump", 0.10, 0.225, 0.50, 0.0000, 0.35),
)

MISPRICING_TYPES = (
    "none",
    "underpriced_variance",
    "overpriced_variance",
    "underpriced_downside_skew",
    "underpriced_upside_skew",
)
MISPRICING_PROBABILITIES = np.array([0.52, 0.15, 0.14, 0.11, 0.08])


@dataclass
class StructureQuote:
    name: str
    family: str
    premium_style: str
    legs: list[tuple[str, float, int]]  # right, strike, quantity (+ long / - short)
    entry_cashflow: float  # positive debit, negative credit
    fair_q_value: float
    physical_expected_payoff: float
    net_q_edge: float
    physical_expected_net: float
    uncertainty: float
    score: float
    max_loss: float
    max_profit: float
    predicted_probability_profit: float
    central_range_probability: float = math.nan
    tail_loss_probability: float = math.nan
    expected_tail_loss: float = math.nan
    cvar_95_loss: float = math.nan
    tail_risk_score: float = math.nan
    range_score: float = math.nan
    adaptive_managed: bool = False


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def lognormal_option_value(
    *,
    spot: float,
    strike: float,
    tenor: float,
    volatility: float,
    right: str,
    underlying_log_drift: float,
    discount_rate: float,
) -> float:
    """Discounted expected option payoff under a lognormal terminal distribution.

    underlying_log_drift is the expected continuously compounded growth rate of E[S_T]/S_0.
    For Q valuation use rate - dividend yield. For P expectation use the forecast drift.
    """
    if tenor <= 0:
        return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    volatility = max(float(volatility), 1e-6)
    root_t = math.sqrt(tenor)
    d1 = (
        math.log(spot / strike)
        + (underlying_log_drift + 0.5 * volatility * volatility) * tenor
    ) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discount = math.exp(-discount_rate * tenor)
    forward_mean = spot * math.exp(underlying_log_drift * tenor)
    if right == "C":
        return discount * (forward_mean * norm_cdf(d1) - strike * norm_cdf(d2))
    return discount * (strike * norm_cdf(-d2) - forward_mean * norm_cdf(-d1))


def option_vol(base_iv: float, skew: float, spot: float, strike: float, right: str) -> float:
    log_moneyness = math.log(max(strike, 1e-9) / spot)
    # Negative skew raises OTM put volatility and lowers OTM call volatility.
    directional_skew = -skew * log_moneyness
    if right == "P":
        directional_skew += 0.015 * max(-log_moneyness, 0.0)
    return float(np.clip(base_iv + directional_skew + 0.35 * log_moneyness**2, 0.04, 1.50))


def option_quote(
    *,
    spot: float,
    strike: float,
    tenor: float,
    right: str,
    market_iv: float,
    market_skew: float,
) -> tuple[float, float, float]:
    vol = option_vol(market_iv, market_skew, spot, strike, right)
    mid = lognormal_option_value(
        spot=spot,
        strike=strike,
        tenor=tenor,
        volatility=vol,
        right=right,
        underlying_log_drift=RATE - DIVIDEND_YIELD,
        discount_rate=RATE,
    )
    # A deliberately conservative synthetic spread model for 0DTE SPY options.
    time_pressure = 0.012 * math.sqrt(max(1.0 / max(tenor * MINUTES_PER_YEAR, 1.0), 0.0))
    spread = max(0.01, min(0.30, 0.012 + 0.035 * mid + time_pressure))
    bid = max(0.0, mid - spread / 2.0)
    ask = mid + spread / 2.0
    return bid, ask, mid


def payoff_from_legs(terminal_spot: float, legs: list[tuple[str, float, int]]) -> float:
    payoff = 0.0
    for right, strike, quantity in legs:
        intrinsic = max(terminal_spot - strike, 0.0) if right == "C" else max(strike - terminal_spot, 0.0)
        payoff += quantity * intrinsic
    return payoff


def _lognormal_cdf(value: float, *, spot: float, tenor: float, volatility: float, drift: float) -> float:
    if value <= 0.0:
        return 0.0
    if tenor <= 0.0 or volatility <= 1e-9:
        terminal = spot * math.exp(drift * max(tenor, 0.0))
        return 1.0 if terminal <= value else 0.0
    mu = math.log(spot) + (drift - 0.5 * volatility * volatility) * tenor
    z = (math.log(value) - mu) / (volatility * math.sqrt(tenor))
    return norm_cdf(z)


def probability_profit_lognormal(
    *,
    legs: list[tuple[str, float, int]],
    entry_cashflow: float,
    spot: float,
    tenor: float,
    volatility: float,
    drift: float,
) -> float:
    """Exact probability of positive expiration P&L for a piecewise-linear option structure."""
    strikes = sorted({float(strike) for _, strike, _ in legs})
    bounds = [0.0, *strikes, math.inf]
    probability = 0.0
    for lower, upper in zip(bounds[:-1], bounds[1:]):
        sample = lower + max(1.0, spot * 0.05) if math.isinf(upper) else 0.5 * (lower + upper)
        slope = 0.0
        intercept = -entry_cashflow
        for right, strike, quantity in legs:
            if right == "C" and sample > strike:
                slope += quantity
                intercept -= quantity * strike
            elif right == "P" and sample < strike:
                slope -= quantity
                intercept += quantity * strike
        lo = lower
        hi = upper
        if abs(slope) < 1e-12:
            if intercept <= 0.0:
                continue
        else:
            root = -intercept / slope
            if slope > 0.0:
                lo = max(lo, root)
            else:
                hi = min(hi, root)
            if hi <= lo:
                continue
        cdf_lo = _lognormal_cdf(lo, spot=spot, tenor=tenor, volatility=volatility, drift=drift)
        cdf_hi = 1.0 if math.isinf(hi) else _lognormal_cdf(
            hi, spot=spot, tenor=tenor, volatility=volatility, drift=drift
        )
        probability += max(0.0, cdf_hi - cdf_lo)
    return float(np.clip(probability, 0.0, 1.0))


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    order = np.argsort(values)
    ordered_values = values[order]
    ordered_weights = weights[order]
    cumulative = np.cumsum(ordered_weights)
    index = int(np.searchsorted(cumulative, quantile, side="left"))
    return float(ordered_values[min(index, len(ordered_values) - 1)])


def physical_tail_metrics(
    *,
    legs: list[tuple[str, float, int]],
    entry_cashflow: float,
    spot: float,
    tenor: float,
    volatility: float,
    drift: float,
    short_put: float,
    short_call: float,
) -> tuple[float, float, float, float]:
    """Return central-range probability, loss probability, expected loss and 95% CVaR.

    Gauss-Hermite quadrature is deterministic and substantially faster than an inner
    Monte Carlo loop while retaining the nonlinear option payoff and lognormal tails.
    """
    nodes, raw_weights = np.polynomial.hermite.hermgauss(31)
    weights = raw_weights / math.sqrt(math.pi)
    sigma = max(float(volatility), 1e-6)
    terminal = spot * np.exp(
        (drift - 0.5 * sigma * sigma) * tenor
        + sigma * math.sqrt(max(tenor, 1e-12)) * math.sqrt(2.0) * nodes
    )
    pnl = np.array([payoff_from_legs(float(value), legs) - entry_cashflow for value in terminal])
    central_probability = float(weights[(terminal > short_put) & (terminal < short_call)].sum())
    loss_probability = float(weights[pnl < 0.0].sum())
    expected_loss = float(np.sum(weights * np.maximum(-pnl, 0.0)))
    losses = np.maximum(-pnl, 0.0)
    loss_q95 = _weighted_quantile(losses, weights, 0.95)
    tail_mask = losses >= loss_q95
    cvar = float(np.sum(weights[tail_mask] * losses[tail_mask]) / max(weights[tail_mask].sum(), 1e-12))
    return central_probability, loss_probability, expected_loss, cvar


def adaptive_iron_condor_specs(
    *,
    spot: float,
    tenor: float,
    market_iv: float,
    model_q_iv: float,
    model_q_skew: float,
    model_p_iv: float,
    model_p_drift_annual: float,
    forecast_remaining_return: float,
    entry_minute: int,
    breadth_5: float,
    concentration_5: float,
) -> list[tuple[str, str, str, list[tuple[str, float, int]], float, float, float]]:
    """Build adaptive balanced and defensive iron-condor candidates.

    Strike locations come from the physical terminal distribution rather than a
    fixed fraction of the market-implied move. Tail allocation is asymmetric when
    drift/skew point to one side, and wing width shrinks as tail risk rises.
    """
    if entry_minute < 180 or entry_minute > 325:
        return []

    physical_move = max(model_p_iv * math.sqrt(max(tenor, 1e-12)), 1e-6)
    directional_ratio = abs(forecast_remaining_return) / physical_move
    breadth_extremity = 2.0 * abs(float(breadth_5) - 0.5)
    broad_move_risk = max(0.0, 0.48 - float(concentration_5)) / 0.48
    normalized_skew = float(np.clip((model_q_skew - 0.14) / 0.45, 0.0, 1.25))
    normalized_vol = float(np.clip(model_p_iv / 0.30, 0.0, 1.50))
    tail_risk_score = float(np.clip(
        0.28 * normalized_vol
        + 0.27 * min(directional_ratio / 0.45, 1.5)
        + 0.20 * breadth_extremity
        + 0.15 * normalized_skew
        + 0.10 * broad_move_risk,
        0.0,
        1.50,
    ))

    q_overpricing = (market_iv - model_q_iv) / max(market_iv, 1e-6)
    p_overpricing = (market_iv - model_p_iv) / max(market_iv, 1e-6)
    range_score = float(
        0.45 * q_overpricing
        + 0.35 * p_overpricing
        - 0.35 * directional_ratio
        - 0.18 * breadth_extremity
        - 0.12 * broad_move_risk
    )

    # Range qualification deliberately rejects broad momentum and correlation-like states.
    if q_overpricing < 0.055 or p_overpricing < 0.10:
        return []
    if directional_ratio > 0.38 or not (0.27 <= breadth_5 <= 0.73):
        return []
    if tail_risk_score > 0.92 or range_score < 0.015:
        return []

    total_bias = float(np.clip(forecast_remaining_return / physical_move, -1.0, 1.0))
    skew_bias = float(np.clip((model_q_skew - 0.22) / 0.50, -0.35, 0.65))
    downside_risk_bias = float(np.clip(-0.55 * total_bias + 0.45 * skew_bias, -0.55, 0.70))

    normal = NormalDist()
    specs = []
    for label, extra_inside, base_wing in (
        ("BALANCED", 0.00, 2.0),
        ("DEFENSIVE", 0.045, 1.0),
    ):
        target_inside = float(np.clip(
            0.855 + 0.065 * tail_risk_score + 0.030 * directional_ratio + extra_inside,
            0.855,
            0.955,
        ))
        total_tail = 1.0 - target_inside
        lower_tail = total_tail * float(np.clip(0.50 - 0.28 * downside_risk_bias, 0.24, 0.72))
        upper_tail = total_tail - lower_tail
        lower_z = normal.inv_cdf(max(lower_tail, 1e-5))
        upper_z = normal.inv_cdf(min(1.0 - upper_tail, 1.0 - 1e-5))
        mu = math.log(spot) + (model_p_drift_annual - 0.5 * model_p_iv**2) * tenor
        scale = model_p_iv * math.sqrt(max(tenor, 1e-12))
        short_put = float(math.floor(math.exp(mu + scale * lower_z)))
        short_call = float(math.ceil(math.exp(mu + scale * upper_z)))
        short_put = min(short_put, math.floor(spot) - 1.0)
        short_call = max(short_call, math.ceil(spot) + 1.0)

        wing = base_wing
        if tail_risk_score >= 0.68:
            wing = 1.0
        elif tail_risk_score <= 0.40 and label == "BALANCED":
            wing = 3.0
        outer_put = max(1.0, short_put - wing)
        outer_call = short_call + wing
        legs = [("P", outer_put, 1), ("P", short_put, -1), ("C", short_call, -1), ("C", outer_call, 1)]
        specs.append((
            f"ADAPTIVE_IRON_CONDOR_{label}",
            "adaptive_short_range",
            "credit",
            legs,
            0.90 if label == "DEFENSIVE" else 0.95,
            tail_risk_score,
            range_score,
        ))
    return specs


def structure_values(
    *,
    name: str,
    family: str,
    premium_style: str,
    legs: list[tuple[str, float, int]],
    spot: float,
    tenor: float,
    market_iv: float,
    market_skew: float,
    model_q_iv: float,
    model_q_skew: float,
    model_p_iv: float,
    model_p_drift_annual: float,
    uncertainty_scale: float,
) -> StructureQuote:
    fair_q = 0.0
    expected_p = 0.0
    raw_entry = 0.0
    total_contracts = 0
    total_spread = 0.0
    for right, strike, quantity in legs:
        bid, ask, _ = option_quote(
            spot=spot,
            strike=strike,
            tenor=tenor,
            right=right,
            market_iv=market_iv,
            market_skew=market_skew,
        )
        raw_entry += quantity * (ask if quantity > 0 else bid)
        total_contracts += abs(quantity)
        total_spread += abs(quantity) * (ask - bid)

        q_vol = option_vol(model_q_iv, model_q_skew, spot, strike, right)
        p_vol = option_vol(model_p_iv, model_q_skew * 0.75, spot, strike, right)
        fair_q += quantity * lognormal_option_value(
            spot=spot,
            strike=strike,
            tenor=tenor,
            volatility=q_vol,
            right=right,
            underlying_log_drift=RATE - DIVIDEND_YIELD,
            discount_rate=RATE,
        )
        expected_p += quantity * lognormal_option_value(
            spot=spot,
            strike=strike,
            tenor=tenor,
            volatility=p_vol,
            right=right,
            underlying_log_drift=model_p_drift_annual,
            discount_rate=0.0,
        )

    slippage = max(0.01, 0.25 * total_spread)
    fees = total_contracts * FEE_PER_CONTRACT / MULTIPLIER
    entry_cashflow = raw_entry + slippage + fees
    net_q_edge = fair_q - entry_cashflow
    physical_expected_net = expected_p - entry_cashflow
    uncertainty = max(
        0.020,
        uncertainty_scale * (
            0.020
            + 0.10 * abs(fair_q)
            + 0.34 * abs(model_q_iv - market_iv) * spot * math.sqrt(max(tenor, 1e-12))
        ),
    )
    score = min(net_q_edge, physical_expected_net) / uncertainty

    call_tail_slope = sum(quantity for right, _, quantity in legs if right == "C")
    if call_tail_slope < 0:
        raise ValueError(f"Unlimited-risk call structure rejected: {name}")

    probe = np.array([0.0, *sorted({strike for _, strike, _ in legs}), spot * 4.0])
    terminal_payoff = np.array([payoff_from_legs(x, legs) for x in probe])
    expiration_pnl = terminal_payoff - entry_cashflow
    max_loss = max(0.0, float(-expiration_pnl.min()))
    max_profit = math.inf if call_tail_slope > 0 else max(0.0, float(expiration_pnl.max()))

    probability_profit = probability_profit_lognormal(
        legs=legs,
        entry_cashflow=entry_cashflow,
        spot=spot,
        tenor=tenor,
        volatility=max(model_p_iv, 1e-6),
        drift=model_p_drift_annual,
    )

    return StructureQuote(
        name=name,
        family=family,
        premium_style=premium_style,
        legs=legs,
        entry_cashflow=float(entry_cashflow),
        fair_q_value=float(fair_q),
        physical_expected_payoff=float(expected_p),
        net_q_edge=float(net_q_edge),
        physical_expected_net=float(physical_expected_net),
        uncertainty=float(uncertainty),
        score=float(score),
        max_loss=float(max_loss),
        max_profit=float(max_profit),
        predicted_probability_profit=probability_profit,
    )


def build_strategy_candidates(
    *,
    spot: float,
    tenor: float,
    market_iv: float,
    market_skew: float,
    model_q_iv: float,
    model_q_skew: float,
    model_p_iv: float,
    model_p_drift_annual: float,
    forecast_remaining_return: float,
    entry_minute: int = 200,
    breadth_5: float = 0.50,
    concentration_5: float = 0.50,
) -> list[StructureQuote]:
    atm = float(round(spot))
    expected_move = max(1.0, spot * market_iv * math.sqrt(max(tenor, 1e-12)))
    body = float(max(1, round(0.70 * expected_move)))
    wing = float(max(1, min(4, round(0.45 * expected_move))))
    width = 2.0
    inner_put = max(1.0, atm - body)
    outer_put = max(1.0, inner_put - wing)
    inner_call = atm + body
    outer_call = inner_call + wing

    specs: list[tuple[str, str, str, list[tuple[str, float, int]], float, float, float]] = [
        ("LONG_CALL", "directional_long", "debit", [("C", atm, 1)], 1.00, math.nan, math.nan),
        ("LONG_PUT", "directional_long", "debit", [("P", atm, 1)], 1.00, math.nan, math.nan),
        ("CALL_DEBIT_VERTICAL", "directional_long", "debit", [("C", atm, 1), ("C", atm + width, -1)], 0.95, math.nan, math.nan),
        ("PUT_DEBIT_VERTICAL", "directional_long", "debit", [("P", atm, 1), ("P", max(1.0, atm - width), -1)], 0.95, math.nan, math.nan),
        ("BULL_PUT_CREDIT_SPREAD", "directional_credit", "credit", [("P", outer_put, 1), ("P", inner_put, -1)], 0.92, math.nan, math.nan),
        ("BEAR_CALL_CREDIT_SPREAD", "directional_credit", "credit", [("C", inner_call, -1), ("C", outer_call, 1)], 0.92, math.nan, math.nan),
        ("LONG_STRADDLE", "long_vol", "debit", [("C", atm, 1), ("P", atm, 1)], 1.05, math.nan, math.nan),
        ("LONG_STRANGLE", "long_vol", "debit", [("P", inner_put, 1), ("C", inner_call, 1)], 1.00, math.nan, math.nan),
        ("REVERSE_IRON_BUTTERFLY", "long_vol", "debit", [("P", inner_put, -1), ("P", atm, 1), ("C", atm, 1), ("C", inner_call, -1)], 1.08, math.nan, math.nan),
        ("REVERSE_IRON_CONDOR", "long_vol", "debit", [("P", outer_put, -1), ("P", inner_put, 1), ("C", inner_call, 1), ("C", outer_call, -1)], 1.08, math.nan, math.nan),
        ("IRON_BUTTERFLY", "short_vol", "credit", [("P", inner_put, 1), ("P", atm, -1), ("C", atm, -1), ("C", inner_call, 1)], 1.00, math.nan, math.nan),
        ("IRON_CONDOR", "short_vol", "credit", [("P", outer_put, 1), ("P", inner_put, -1), ("C", inner_call, -1), ("C", outer_call, 1)], 0.95, math.nan, math.nan),
        ("CALL_BUTTERFLY", "range_debit", "debit", [("C", atm - width, 1), ("C", atm, -2), ("C", atm + width, 1)], 1.00, math.nan, math.nan),
        ("PUT_BUTTERFLY", "range_debit", "debit", [("P", max(1.0, atm - width), 1), ("P", atm, -2), ("P", atm + width, 1)], 1.00, math.nan, math.nan),
        ("CALL_CONDOR", "range_debit", "debit", [("C", atm - 2*width, 1), ("C", atm - width, -1), ("C", atm + width, -1), ("C", atm + 2*width, 1)], 1.04, math.nan, math.nan),
        ("PUT_CONDOR", "range_debit", "debit", [("P", max(1.0, atm - 2*width), 1), ("P", max(1.0, atm - width), -1), ("P", atm + width, -1), ("P", atm + 2*width, 1)], 1.04, math.nan, math.nan),
        ("CALL_BROKEN_WING_BUTTERFLY", "defined_tail", "conditional", [("C", atm, 1), ("C", atm + width, -2), ("C", atm + 3*width, 1)], 1.08, math.nan, math.nan),
        ("PUT_BROKEN_WING_BUTTERFLY", "defined_tail", "conditional", [("P", atm, 1), ("P", max(1.0, atm - width), -2), ("P", max(1.0, atm - 3*width), 1)], 1.08, math.nan, math.nan),
        ("CALL_BACKSPREAD", "convex_tail", "conditional", [("C", atm, -1), ("C", atm + width, 2)], 1.10, math.nan, math.nan),
        ("PUT_BACKSPREAD", "convex_tail", "conditional", [("P", atm, -1), ("P", max(1.0, atm - width), 2)], 1.10, math.nan, math.nan),
    ]
    specs.extend(adaptive_iron_condor_specs(
        spot=spot,
        tenor=tenor,
        market_iv=market_iv,
        model_q_iv=model_q_iv,
        model_q_skew=model_q_skew,
        model_p_iv=model_p_iv,
        model_p_drift_annual=model_p_drift_annual,
        forecast_remaining_return=forecast_remaining_return,
        entry_minute=entry_minute,
        breadth_5=breadth_5,
        concentration_5=concentration_5,
    ))

    direction = 1 if forecast_remaining_return >= 0 else -1
    vol_gap = model_q_iv - market_iv
    skew_gap = model_q_skew - market_skew
    candidates: list[StructureQuote] = []
    for name, family, premium_style, legs, uncertainty_scale, tail_risk_score, range_score in specs:
        # Directional filtering avoids pretending that bullish and bearish structures are equivalent.
        if name in {"LONG_CALL", "CALL_DEBIT_VERTICAL", "BULL_PUT_CREDIT_SPREAD", "CALL_BROKEN_WING_BUTTERFLY", "CALL_BACKSPREAD"} and direction < 0:
            continue
        if name in {"LONG_PUT", "PUT_DEBIT_VERTICAL", "BEAR_CALL_CREDIT_SPREAD", "PUT_BROKEN_WING_BUTTERFLY", "PUT_BACKSPREAD"} and direction > 0:
            continue
        if family == "long_vol" and vol_gap < 0.001 and abs(skew_gap) < 0.020:
            continue
        if family == "short_vol" and (vol_gap > -0.001 or abs(forecast_remaining_return) > 0.0075):
            continue
        if family == "range_debit" and abs(forecast_remaining_return) > 0.0045:
            continue
        if family == "directional_credit" and vol_gap > 0.006:
            continue
        if family == "convex_tail" and abs(forecast_remaining_return) < 0.0012 and abs(skew_gap) < 0.025:
            continue
        candidate = structure_values(
            name=name,
            family=family,
            premium_style=premium_style,
            legs=legs,
            spot=spot,
            tenor=tenor,
            market_iv=market_iv,
            market_skew=market_skew,
            model_q_iv=model_q_iv,
            model_q_skew=model_q_skew,
            model_p_iv=model_p_iv,
            model_p_drift_annual=model_p_drift_annual,
            uncertainty_scale=uncertainty_scale,
        )
        # Enforce the intended debit/credit identity after realistic entry costs.
        if premium_style == "credit" and candidate.entry_cashflow >= 0.0:
            continue
        if premium_style == "debit" and candidate.entry_cashflow <= 0.0:
            continue
        if family == "adaptive_short_range":
            short_put = next(strike for right, strike, quantity in legs if right == "P" and quantity < 0)
            short_call = next(strike for right, strike, quantity in legs if right == "C" and quantity < 0)
            central, loss_probability, expected_loss, cvar = physical_tail_metrics(
                legs=legs,
                entry_cashflow=candidate.entry_cashflow,
                spot=spot,
                tenor=tenor,
                volatility=model_p_iv,
                drift=model_p_drift_annual,
                short_put=short_put,
                short_call=short_call,
            )
            candidate.central_range_probability = central
            candidate.tail_loss_probability = loss_probability
            candidate.expected_tail_loss = expected_loss
            candidate.cvar_95_loss = cvar
            candidate.tail_risk_score = tail_risk_score
            candidate.range_score = range_score
            candidate.adaptive_managed = True
        candidates.append(candidate)
    return candidates


def candidate_passes(candidate: StructureQuote) -> bool:
    actual_style = "credit" if candidate.entry_cashflow < 0.0 else "debit"
    max_loss = candidate.max_loss
    if not np.isfinite(max_loss) or max_loss <= 0.0 or max_loss > 6.00:
        return False
    if candidate.physical_expected_net / max(max_loss, 1e-9) < 0.010:
        return False
    if candidate.family == "adaptive_short_range":
        credit = -candidate.entry_cashflow
        reward_to_risk = credit / max(max_loss, 1e-9)
        expected_tail_ratio = candidate.expected_tail_loss / max(max_loss, 1e-9)
        cvar_ratio = candidate.cvar_95_loss / max(max_loss, 1e-9)
        return bool(
            credit >= 0.055
            and max_loss <= 2.75
            and reward_to_risk >= 0.20
            and candidate.net_q_edge >= 0.010
            and candidate.physical_expected_net >= 0.012
            and candidate.score >= 0.10
            and candidate.predicted_probability_profit >= 0.76
            and candidate.central_range_probability >= 0.82
            and candidate.tail_loss_probability <= 0.24
            and expected_tail_ratio <= 0.15
            and cvar_ratio <= 0.92
            and candidate.tail_risk_score <= 0.86
            and candidate.range_score >= 0.020
        )
    if actual_style == "credit":
        credit = -candidate.entry_cashflow
        return bool(
            credit >= 0.025
            and candidate.net_q_edge >= 0.015
            and candidate.physical_expected_net >= 0.015
            and candidate.score >= 0.18
            and candidate.predicted_probability_profit >= 0.52
        )
    if candidate.family in {"long_vol", "convex_tail"}:
        return bool(
            candidate.net_q_edge >= 0.025
            and candidate.physical_expected_net >= 0.025
            and candidate.score >= 0.22
            and candidate.predicted_probability_profit >= 0.34
        )
    return bool(
        candidate.net_q_edge >= 0.025
        and candidate.physical_expected_net >= 0.025
        and candidate.score >= 0.22
        and candidate.predicted_probability_profit >= 0.40
    )


def liquidation_cashflow(
    candidate: StructureQuote,
    *,
    spot: float,
    tenor: float,
    market_iv: float,
    market_skew: float,
) -> float:
    raw = 0.0
    total_spread = 0.0
    total_contracts = 0
    for right, strike, quantity in candidate.legs:
        bid, ask, _ = option_quote(
            spot=spot,
            strike=strike,
            tenor=max(tenor, 1e-12),
            right=right,
            market_iv=market_iv,
            market_skew=market_skew,
        )
        raw += quantity * (bid if quantity > 0 else ask)
        total_spread += abs(quantity) * (ask - bid)
        total_contracts += abs(quantity)
    extra_slippage = max(0.005, 0.10 * total_spread)
    fees = total_contracts * FEE_PER_CONTRACT / MULTIPLIER
    return float(raw - extra_slippage - fees)


def adaptive_condor_liquidation(
    candidate: StructureQuote,
    *,
    entry_minute: int,
    default_exit_minute: int,
    spy_prices: np.ndarray,
    market_iv: np.ndarray,
    market_skew: np.ndarray,
    model_q_iv: np.ndarray,
) -> tuple[float, int, float, str]:
    """Manage adaptive condors with profit, loss, range, and edge-invalidation exits."""
    credit = max(-candidate.entry_cashflow, 0.0)
    profit_target = max(0.035, 0.52 * credit)
    stop_loss = min(0.58 * candidate.max_loss, max(1.20 * credit, 0.20 * candidate.max_loss))
    short_put = next(strike for right, strike, quantity in candidate.legs if right == "P" and quantity < 0)
    short_call = next(strike for right, strike, quantity in candidate.legs if right == "C" and quantity < 0)
    outer_put = next(strike for right, strike, quantity in candidate.legs if right == "P" and quantity > 0)
    outer_call = next(strike for right, strike, quantity in candidate.legs if right == "C" and quantity > 0)
    put_buffer = 0.30 * max(short_put - outer_put, 1.0)
    call_buffer = 0.30 * max(outer_call - short_call, 1.0)

    final_cashflow = liquidation_cashflow(
        candidate,
        spot=float(spy_prices[default_exit_minute]),
        tenor=(MINUTES_PER_DAY - default_exit_minute) / MINUTES_PER_YEAR,
        market_iv=float(market_iv[default_exit_minute]),
        market_skew=float(market_skew[default_exit_minute]),
    )
    final_reason = "scheduled_exit"
    final_minute = default_exit_minute

    for minute in range(entry_minute + 5, default_exit_minute + 1, 5):
        cashflow = liquidation_cashflow(
            candidate,
            spot=float(spy_prices[minute]),
            tenor=(MINUTES_PER_DAY - minute) / MINUTES_PER_YEAR,
            market_iv=float(market_iv[minute]),
            market_skew=float(market_skew[minute]),
        )
        pnl = cashflow - candidate.entry_cashflow
        spot = float(spy_prices[minute])
        if pnl >= profit_target:
            return cashflow, minute, spot, "profit_target"
        if pnl <= -stop_loss:
            return cashflow, minute, spot, "tail_stop"
        if pnl < 0.0 and (spot <= short_put + put_buffer or spot >= short_call - call_buffer):
            return cashflow, minute, spot, "soft_range_breach"
        # Exit if the constituent-implied overpricing that justified the sale disappears.
        if minute >= entry_minute + 20 and market_iv[minute] - model_q_iv[minute] < 0.0015 and pnl > -0.10 * candidate.max_loss:
            return cashflow, minute, spot, "edge_invalidation"
        final_cashflow = cashflow
        final_minute = minute

    return final_cashflow, final_minute, float(spy_prices[final_minute]), final_reason

def choose_regime(rng: np.random.Generator) -> Regime:
    probabilities = np.array([regime.probability for regime in REGIMES], dtype=float)
    probabilities /= probabilities.sum()
    return REGIMES[int(rng.choice(len(REGIMES), p=probabilities))]


def time_of_day_multiplier() -> np.ndarray:
    x = np.linspace(-1.0, 1.0, MINUTES_PER_DAY)
    return 0.76 + 0.46 * np.abs(x) ** 1.55


def rolling_std(values: np.ndarray, end: int, window: int, fallback: float) -> float:
    start = max(0, end - window + 1)
    chunk = values[start : end + 1]
    if len(chunk) < 5:
        return fallback
    value = float(np.std(chunk, ddof=1))
    return value if np.isfinite(value) and value > 0 else fallback


def create_universe(rng: np.random.Generator, constituents: int = 50, sectors: int = 10):
    tickers = np.array([f"S{i:03d}" for i in range(constituents)])
    raw_weights = rng.lognormal(mean=0.0, sigma=1.05, size=constituents)
    raw_weights.sort()
    raw_weights = raw_weights[::-1]
    weights = raw_weights / raw_weights.sum()
    sector_ids = np.arange(constituents) % sectors
    beta = rng.uniform(0.72, 1.38, size=constituents)
    idio_multiplier = rng.uniform(0.85, 1.65, size=constituents)
    return tickers, weights, sector_ids, beta, idio_multiplier


def simulate_world(
    *,
    world: int,
    rng: np.random.Generator,
    weights: np.ndarray,
    sector_ids: np.ndarray,
    beta: np.ndarray,
    idio_multiplier: np.ndarray,
) -> tuple[dict, list[dict]]:
    regime = choose_regime(rng)
    constituents = len(weights)
    sectors = int(sector_ids.max()) + 1
    tod = time_of_day_multiplier()

    mispricing_type = str(rng.choice(MISPRICING_TYPES, p=MISPRICING_PROBABILITIES))
    episode_start = int(rng.integers(50, 280))
    episode_duration = int(rng.integers(25, 85))
    episode_end = min(MINUTES_PER_DAY - 20, episode_start + episode_duration)
    episode_strength = float(rng.uniform(0.08, 0.22))

    event_type = str(rng.choice(
        ["none", "broad_up", "broad_down", "sector_up", "sector_down", "mega_up", "mega_down"],
        p=[0.28, 0.14, 0.14, 0.11, 0.11, 0.11, 0.11],
    ))
    event_start = int(rng.integers(35, 285))
    event_duration = int(rng.integers(12, 46))
    event_amplitude = float(rng.uniform(0.0020, 0.0100))

    # Regime-dependent dynamic correlation and volatility.
    base_corr = regime.average_correlation
    if regime.name == "correlation_shock":
        corr_shock_start = int(rng.integers(80, 260))
    else:
        corr_shock_start = MINUTES_PER_DAY + 1

    constituent_returns = np.zeros((MINUTES_PER_DAY, constituents), dtype=float)
    index_returns = np.zeros(MINUTES_PER_DAY, dtype=float)
    latent_index_iv = np.zeros(MINUTES_PER_DAY, dtype=float)
    realized_corr_proxy = np.zeros(MINUTES_PER_DAY, dtype=float)

    market_state = 0.0
    sector_state = np.zeros(sectors)
    daily_drift_per_minute = regime.daily_drift / MINUTES_PER_DAY

    for minute in range(MINUTES_PER_DAY):
        corr = base_corr
        annual_vol = regime.annual_vol
        if minute >= corr_shock_start:
            corr = min(0.88, corr + 0.16)
            annual_vol *= 1.18
        if regime.name in {"bear", "high_vol", "correlation_shock"} and market_state < -0.5:
            corr = min(0.92, corr + 0.08)

        minute_index_sigma = annual_vol / math.sqrt(MINUTES_PER_YEAR) * tod[minute]
        market_state = 0.18 * market_state + rng.normal()
        sector_state = 0.10 * sector_state + rng.normal(size=sectors)
        idio = rng.normal(size=constituents)

        common_loading = math.sqrt(max(corr, 0.02))
        sector_loading = math.sqrt(max(min(0.18, 0.65 * (1.0 - corr)), 0.02))
        idio_loading = np.sqrt(np.maximum(1.0 - common_loading**2 - sector_loading**2, 0.08))

        stock_sigma = minute_index_sigma * idio_multiplier
        raw = (
            beta * common_loading * market_state
            + sector_loading * sector_state[sector_ids]
            + idio_loading * idio
        )
        minute_returns = stock_sigma * raw + daily_drift_per_minute * beta

        if event_start <= minute < event_start + event_duration:
            progress = (minute - event_start) / max(event_duration - 1, 1)
            pulse = math.sin(math.pi * progress)
            sign = 1.0 if event_type.endswith("up") else -1.0
            per_minute_impulse = sign * event_amplitude / max(event_duration, 1) * (0.65 + 0.70 * pulse)
            if event_type.startswith("broad"):
                minute_returns += per_minute_impulse * (0.75 + 0.50 * beta)
            elif event_type.startswith("sector"):
                target_sector = world % sectors
                minute_returns[sector_ids == target_sector] += per_minute_impulse * 2.25
            elif event_type.startswith("mega"):
                top = max(2, constituents // 10)
                minute_returns[:top] += per_minute_impulse * 4.0

        # Occasional jump, especially in the jump regime.
        if rng.random() < regime.jump_probability / MINUTES_PER_DAY:
            jump = rng.normal(loc=-0.006 if regime.name != "bull" else 0.003, scale=0.008)
            minute_returns += jump * beta

        constituent_returns[minute] = minute_returns
        index_returns[minute] = float(minute_returns @ weights)
        latent_index_iv[minute] = annual_vol * tod[minute] * (0.92 + 0.18 * corr / max(base_corr, 0.05))
        realized_corr_proxy[minute] = corr

    spy_prices = 500.0 * np.exp(np.cumsum(index_returns))

    # Constituent-implied and market-implied surface states update at different speeds.
    constituent_q_iv = np.zeros(MINUTES_PER_DAY)
    raw_market_iv = np.zeros(MINUTES_PER_DAY)
    model_q_iv = np.zeros(MINUTES_PER_DAY)
    model_p_iv = np.zeros(MINUTES_PER_DAY)
    model_q_skew = np.zeros(MINUTES_PER_DAY)
    raw_market_skew = np.zeros(MINUTES_PER_DAY)
    risk_premium_multiple = 1.12 if regime.name not in {"high_vol", "correlation_shock"} else 1.18
    raw_market_iv[0] = latent_index_iv[0] * risk_premium_multiple
    constituent_q_iv[0] = raw_market_iv[0]
    raw_market_skew[0] = 0.22
    model_q_skew[0] = 0.22

    for minute in range(MINUTES_PER_DAY):
        recent_rv = rolling_std(
            index_returns,
            minute,
            30,
            latent_index_iv[minute] / math.sqrt(MINUTES_PER_YEAR),
        ) * math.sqrt(MINUTES_PER_YEAR)
        downside_pressure = max(0.0, -np.sum(index_returns[max(0, minute - 9) : minute + 1]))
        broad_down = float(
            np.average(
                np.sum(constituent_returns[max(0, minute - 4) : minute + 1], axis=0) < 0,
                weights=weights,
            )
        )
        target_constituent_iv = (
            0.62 * latent_index_iv[minute]
            + 0.38 * recent_rv
        ) * risk_premium_multiple
        if minute == 0:
            constituent_q_iv[minute] = target_constituent_iv
        else:
            constituent_q_iv[minute] = (
                0.76 * constituent_q_iv[minute - 1]
                + 0.24 * target_constituent_iv
                + rng.normal(0.0, 0.0018)
            )
        model_q_iv[minute] = max(0.05, constituent_q_iv[minute] + rng.normal(0.0, 0.0035))
        model_p_iv[minute] = max(
            0.04,
            0.55 * recent_rv + 0.45 * (model_q_iv[minute] / risk_premium_multiple),
        )
        model_q_skew[minute] = float(np.clip(0.18 + 8.5 * downside_pressure + 0.12 * broad_down, 0.08, 0.75))

        if minute > 0:
            raw_market_iv[minute] = (
                0.955 * raw_market_iv[minute - 1]
                + 0.045 * constituent_q_iv[max(0, minute - 5)]
                + rng.normal(0.0, 0.0012)
            )
            raw_market_skew[minute] = (
                0.965 * raw_market_skew[minute - 1]
                + 0.035 * model_q_skew[max(0, minute - 6)]
                + rng.normal(0.0, 0.004)
            )
        raw_market_iv[minute] = float(np.clip(raw_market_iv[minute], 0.05, 0.85))
        raw_market_skew[minute] = float(np.clip(raw_market_skew[minute], 0.04, 0.90))

    # Apply each controlled surface discrepancy to the clean market path without
    # recursively compounding it through the market-IV state process.
    market_iv = raw_market_iv.copy()
    market_skew = raw_market_skew.copy()
    if mispricing_type != "none":
        for minute in range(episode_start, episode_end + 1):
            shape = math.sin(math.pi * (minute - episode_start) / max(episode_end - episode_start, 1))
            strength = episode_strength * max(shape, 0.0)
            if mispricing_type == "underpriced_variance":
                market_iv[minute] = raw_market_iv[minute] * (1.0 - strength)
            elif mispricing_type == "overpriced_variance":
                market_iv[minute] = raw_market_iv[minute] * (1.0 + strength)
            elif mispricing_type == "underpriced_downside_skew":
                market_skew[minute] = raw_market_skew[minute] * (1.0 - 0.85 * strength)
            elif mispricing_type == "underpriced_upside_skew":
                market_skew[minute] = raw_market_skew[minute] * (1.0 + 0.65 * strength)
    market_iv = np.clip(market_iv, 0.05, 0.85)
    market_skew = np.clip(market_skew, 0.04, 0.90)

    best_by_strategy: dict[str, tuple[StructureQuote, dict]] = {}
    max_candidate_score = -1e9
    max_candidate_q_edge = -1e9
    max_candidate_p_edge = -1e9
    max_candidate_pop = 0.0
    best_candidate_name = ""
    min_entry_minute = 40
    max_entry_minute = 330
    exit_minute = 385

    # Five-minute decision grid. The underlying market still evolves at one-minute resolution.
    for minute in range(min_entry_minute, max_entry_minute + 1, 5):
        remaining_minutes = MINUTES_PER_DAY - minute
        tenor = remaining_minutes / MINUTES_PER_YEAR
        spot = float(spy_prices[minute])
        cumulative_5 = constituent_returns[minute - 4 : minute + 1].sum(axis=0)
        cumulative_15 = constituent_returns[minute - 14 : minute + 1].sum(axis=0)
        pressure_5 = float(cumulative_5 @ weights)
        pressure_15 = float(cumulative_15 @ weights)
        breadth_5 = float(np.average(cumulative_5 > 0, weights=weights))
        equal_weight_5 = float(cumulative_5.mean())
        top_n = max(3, len(weights) // 10)
        contribution = weights * cumulative_5
        concentration = float(np.sum(np.abs(contribution[:top_n])) / max(np.sum(np.abs(contribution)), 1e-10))
        breadth_confirmation = 2.0 * abs(breadth_5 - 0.5)
        concentration_penalty = max(0.35, 1.0 - 0.85 * max(concentration - 0.35, 0.0))
        persistence_minutes = min(remaining_minutes, 38)
        forecast_per_minute = (
            0.11 * pressure_5 / 5.0
            + 0.055 * pressure_15 / 15.0
            + 0.035 * equal_weight_5 / 5.0
        )
        forecast_remaining_return = (
            forecast_per_minute
            * persistence_minutes
            * (0.55 + 0.95 * breadth_confirmation)
            * concentration_penalty
        )
        forecast_remaining_return = float(np.clip(0.75 * forecast_remaining_return, -0.022, 0.022))
        model_p_drift_annual = forecast_remaining_return / max(tenor, 1e-9)

        surface_vol_gap = float(model_q_iv[minute] - market_iv[minute])
        surface_skew_gap = float(model_q_skew[minute] - market_skew[minute])
        if abs(surface_vol_gap) < 0.0015 and abs(surface_skew_gap) < 0.018 and abs(forecast_remaining_return) < 0.0009:
            continue

        candidates = build_strategy_candidates(
            spot=spot,
            tenor=tenor,
            market_iv=float(market_iv[minute]),
            market_skew=float(market_skew[minute]),
            model_q_iv=float(model_q_iv[minute]),
            model_q_skew=float(model_q_skew[minute]),
            model_p_iv=float(model_p_iv[minute]),
            model_p_drift_annual=model_p_drift_annual,
            forecast_remaining_return=forecast_remaining_return,
            entry_minute=minute,
            breadth_5=breadth_5,
            concentration_5=concentration,
        )
        for candidate in candidates:
            if candidate.score > max_candidate_score:
                max_candidate_score = candidate.score
                max_candidate_q_edge = candidate.net_q_edge
                max_candidate_p_edge = candidate.physical_expected_net
                max_candidate_pop = candidate.predicted_probability_profit
                best_candidate_name = candidate.name
            if not candidate_passes(candidate):
                continue
            selection_score = (
                candidate.score
                + 0.16 * candidate.predicted_probability_profit
                + 0.08 * min(candidate.physical_expected_net / max(candidate.max_loss, 1e-9), 1.0)
            )
            if candidate.family == "adaptive_short_range":
                selection_score += (
                    0.20 * candidate.range_score
                    + 0.10 * candidate.central_range_probability
                    - 0.12 * candidate.tail_risk_score
                    - 0.08 * candidate.cvar_95_loss / max(candidate.max_loss, 1e-9)
                )
            prior = best_by_strategy.get(candidate.name)
            if prior is None or selection_score > prior[1]["selection_score"]:
                best_by_strategy[candidate.name] = (
                    candidate,
                    {
                        "selection_score": float(selection_score),
                        "entry_minute": minute,
                        "minutes_remaining": remaining_minutes,
                        "spot_entry": spot,
                        "market_iv": float(market_iv[minute]),
                        "model_q_iv": float(model_q_iv[minute]),
                        "model_p_iv": float(model_p_iv[minute]),
                        "market_skew": float(market_skew[minute]),
                        "model_q_skew": float(model_q_skew[minute]),
                        "forecast_remaining_return": forecast_remaining_return,
                        "breadth_5": breadth_5,
                        "concentration_5": concentration,
                        "surface_vol_gap": surface_vol_gap,
                        "surface_skew_gap": surface_skew_gap,
                    },
                )

    trades: list[dict] = []
    exit_spot = float(spy_prices[exit_minute])
    exit_tenor = (MINUTES_PER_DAY - exit_minute) / MINUTES_PER_YEAR
    terminal_spot = float(spy_prices[-1])
    for candidate, metadata in best_by_strategy.values():
        trade_exit_minute = exit_minute
        trade_exit_spot = exit_spot
        exit_reason = "scheduled_exit"
        if candidate.adaptive_managed:
            exit_cashflow, trade_exit_minute, trade_exit_spot, exit_reason = adaptive_condor_liquidation(
                candidate,
                entry_minute=int(metadata["entry_minute"]),
                default_exit_minute=exit_minute,
                spy_prices=spy_prices,
                market_iv=market_iv,
                market_skew=market_skew,
                model_q_iv=model_q_iv,
            )
        else:
            exit_cashflow = liquidation_cashflow(
                candidate,
                spot=exit_spot,
                tenor=exit_tenor,
                market_iv=float(market_iv[exit_minute]),
                market_skew=float(market_skew[exit_minute]),
            )
        pnl_per_share = exit_cashflow - candidate.entry_cashflow
        actual_style = "credit" if candidate.entry_cashflow < 0.0 else "debit"
        trade = {
            "world": world,
            "regime": regime.name,
            "mispricing_type": mispricing_type,
            "event_type": event_type,
            "entry_minute": int(metadata["entry_minute"]),
            "exit_minute": trade_exit_minute,
            "exit_reason": exit_reason,
            "minutes_remaining": int(metadata["minutes_remaining"]),
            "structure": candidate.name,
            "family": candidate.family,
            "premium_style": actual_style,
            "spot_entry": float(metadata["spot_entry"]),
            "exit_spot": trade_exit_spot,
            "terminal_spot": terminal_spot,
            "market_iv": float(metadata["market_iv"]),
            "model_q_iv": float(metadata["model_q_iv"]),
            "model_p_iv": float(metadata["model_p_iv"]),
            "market_skew": float(metadata["market_skew"]),
            "model_q_skew": float(metadata["model_q_skew"]),
            "surface_vol_gap": float(metadata["surface_vol_gap"]),
            "surface_skew_gap": float(metadata["surface_skew_gap"]),
            "forecast_remaining_return": float(metadata["forecast_remaining_return"]),
            "actual_remaining_return": trade_exit_spot / float(metadata["spot_entry"]) - 1.0,
            "breadth_5": float(metadata["breadth_5"]),
            "concentration_5": float(metadata["concentration_5"]),
            "entry_cashflow": candidate.entry_cashflow,
            "exit_cashflow": exit_cashflow,
            "fair_q_value": candidate.fair_q_value,
            "net_q_edge": candidate.net_q_edge,
            "physical_expected_net": candidate.physical_expected_net,
            "uncertainty": candidate.uncertainty,
            "edge_score": candidate.score,
            "selection_score": float(metadata["selection_score"]),
            "predicted_probability_profit": candidate.predicted_probability_profit,
            "central_range_probability": candidate.central_range_probability,
            "tail_loss_probability": candidate.tail_loss_probability,
            "expected_tail_loss": candidate.expected_tail_loss * MULTIPLIER if np.isfinite(candidate.expected_tail_loss) else math.nan,
            "cvar_95_loss": candidate.cvar_95_loss * MULTIPLIER if np.isfinite(candidate.cvar_95_loss) else math.nan,
            "tail_risk_score": candidate.tail_risk_score,
            "range_score": candidate.range_score,
            "adaptive_managed": candidate.adaptive_managed,
            "max_loss": candidate.max_loss * MULTIPLIER,
            "max_profit": candidate.max_profit * MULTIPLIER if np.isfinite(candidate.max_profit) else math.inf,
            "pnl": pnl_per_share * MULTIPLIER,
            "profitable": pnl_per_share > 0.0,
            "injected_edge_active": episode_start <= int(metadata["entry_minute"]) <= episode_end and mispricing_type != "none",
            "episode_start": episode_start,
            "episode_end": episode_end,
            "event_start": event_start,
            "legs": json.dumps(candidate.legs),
        }
        trades.append(trade)

    world_row = {
        "world": world,
        "regime": regime.name,
        "mispricing_type": mispricing_type,
        "event_type": event_type,
        "episode_start": episode_start,
        "episode_end": episode_end,
        "event_start": event_start,
        "terminal_spot": float(spy_prices[-1]),
        "daily_return": float(spy_prices[-1] / 500.0 - 1.0),
        "realized_vol_annualized": float(np.std(index_returns, ddof=1) * math.sqrt(MINUTES_PER_YEAR)),
        "average_latent_iv": float(np.mean(latent_index_iv)),
        "average_market_iv": float(np.mean(market_iv)),
        "average_model_q_iv": float(np.mean(model_q_iv)),
        "average_correlation": float(np.mean(realized_corr_proxy)),
        "trade_taken": bool(trades),
        "eligible_strategy_count": len(trades),
        "premium_selling_count": sum(t["premium_style"] == "credit" for t in trades),
        "aggregate_candidate_pnl": float(sum(t["pnl"] for t in trades)),
        "max_candidate_score": float(max_candidate_score),
        "max_candidate_q_edge": float(max_candidate_q_edge),
        "max_candidate_p_edge": float(max_candidate_p_edge),
        "max_candidate_pop": float(max_candidate_pop),
        "best_candidate_name": best_candidate_name,
    }
    return world_row, trades


def calibration_table(trades: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["bin", "trades", "mean_predicted_probability", "realized_win_rate"])
    labels = pd.IntervalIndex.from_breaks(np.linspace(0.0, 1.0, bins + 1), closed="right")
    bucket = pd.cut(
        trades["predicted_probability_profit"].clip(0.0, 1.0),
        bins=np.linspace(0.0, 1.0, bins + 1),
        include_lowest=True,
    )
    grouped = trades.assign(bin=bucket).groupby("bin", observed=False)
    result = grouped.agg(
        trades=("profitable", "size"),
        mean_predicted_probability=("predicted_probability_profit", "mean"),
        realized_win_rate=("profitable", "mean"),
    ).reset_index()
    result["bin"] = result["bin"].astype(str)
    return result[result["trades"] > 0]


def build_summary(worlds: pd.DataFrame, trades: pd.DataFrame) -> dict:
    if trades.empty:
        pnl = np.array([], dtype=float)
        probabilities = np.array([], dtype=float)
        outcomes = np.array([], dtype=float)
    else:
        pnl = trades["pnl"].to_numpy(dtype=float)
        probabilities = trades["predicted_probability_profit"].to_numpy(dtype=float)
        outcomes = trades["profitable"].astype(float).to_numpy()
    gross_profit = float(pnl[pnl > 0].sum()) if len(pnl) else 0.0
    gross_loss = float(-pnl[pnl < 0].sum()) if len(pnl) else 0.0
    return {
        "simulation": {
            "worlds": int(len(worlds)),
            "minutes_per_world": MINUTES_PER_DAY,
            "total_simulated_minutes": int(len(worlds) * MINUTES_PER_DAY),
            "constituents": 50,
            "resolution": "1 minute",
            "decision_grid": "5 minutes",
            "holding_period": "entry to forced 3:55 PM liquidation",
            "strategy_scope": "single-expiration, no stock ownership, no unlimited risk",
            "seed": None,
        },
        "tournament": {
            "independent_opportunities": int(len(trades)),
            "worlds_with_opportunities": int(worlds["trade_taken"].sum()),
            "world_opportunity_rate": float(worlds["trade_taken"].mean()),
            "average_opportunities_per_world": float(len(trades) / max(len(worlds), 1)),
            "credit_opportunities": int((trades["premium_style"] == "credit").sum()) if not trades.empty else 0,
            "credit_share": float((trades["premium_style"] == "credit").mean()) if not trades.empty else 0.0,
            "aggregate_independent_pnl": float(pnl.sum()) if len(pnl) else 0.0,
            "average_pnl": float(pnl.mean()) if len(pnl) else 0.0,
            "median_pnl": float(np.median(pnl)) if len(pnl) else 0.0,
            "win_rate": float(outcomes.mean()) if len(outcomes) else 0.0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0),
            "worst_trade": float(pnl.min()) if len(pnl) else 0.0,
            "best_trade": float(pnl.max()) if len(pnl) else 0.0,
        },
        "calibration": {
            "brier_score": float(brier_score(probabilities, outcomes)) if len(probabilities) else None,
            "log_score": float(log_score(probabilities, outcomes)) if len(probabilities) else None,
            "expected_calibration_error": float(calibration_error(probabilities, outcomes)) if len(probabilities) else None,
            "mean_predicted_probability": float(probabilities.mean()) if len(probabilities) else None,
            "realized_win_rate": float(outcomes.mean()) if len(outcomes) else None,
        },
        "world_distribution": {
            "mean_daily_return": float(worlds["daily_return"].mean()),
            "daily_return_std": float(worlds["daily_return"].std(ddof=1)),
            "mean_realized_vol": float(worlds["realized_vol_annualized"].mean()),
            "p05_daily_return": float(worlds["daily_return"].quantile(0.05)),
            "p95_daily_return": float(worlds["daily_return"].quantile(0.95)),
        },
    }


def write_report(
    output_dir: Path,
    summary: dict,
    regime_metrics: pd.DataFrame,
    structure_metrics: pd.DataFrame,
    family_metrics: pd.DataFrame,
    premium_metrics: pd.DataFrame,
    mispricing_metrics: pd.DataFrame,
) -> None:
    t = summary["tournament"]
    c = summary["calibration"]
    lines = [
        "# Full Defined-Risk Intraday Strategy Tournament",
        "",
        "## Scope",
        "",
        "- One-minute synthetic market worlds with decisions every five minutes",
        "- Forced liquidation at 3:55 PM; no overnight holdings or stock assignment",
        "- No stock ownership and no unlimited-risk structures",
        "- Includes debit, credit, range, convex-tail, butterfly, condor, and backspread families",
        "- Each strategy is evaluated independently; aggregate tournament P&L is not a deployable portfolio",
        "",
        "## Tournament Results",
        "",
        f"- Independent opportunities: **{t['independent_opportunities']}**",
        f"- Worlds with at least one opportunity: **{t['world_opportunity_rate']:.1%}**",
        f"- Average opportunities per world: **{t['average_opportunities_per_world']:.2f}**",
        f"- Premium-selling share: **{t['credit_share']:.1%}**",
        f"- Independent aggregate P&L: **${t['aggregate_independent_pnl']:,.2f}**",
        f"- Average / median P&L: **${t['average_pnl']:,.2f} / ${t['median_pnl']:,.2f}**",
        f"- Win rate: **{t['win_rate']:.1%}**",
        f"- Profit factor: **{t['profit_factor']:.2f}**",
        f"- Worst / best trade: **${t['worst_trade']:,.2f} / ${t['best_trade']:,.2f}**",
        "",
        "## Calibration",
        "",
        f"- Mean predicted probability: **{c['mean_predicted_probability']:.1%}**" if c['mean_predicted_probability'] is not None else "- No calibration data",
        f"- Realized win rate: **{c['realized_win_rate']:.1%}**" if c['realized_win_rate'] is not None else "",
        f"- Expected calibration error: **{c['expected_calibration_error']:.4f}**" if c['expected_calibration_error'] is not None else "",
        "",
        "## By Strategy",
        "",
        structure_metrics.to_markdown(index=False, floatfmt=".3f") if not structure_metrics.empty else "No results.",
        "",
        "## By Family",
        "",
        family_metrics.to_markdown(index=False, floatfmt=".3f") if not family_metrics.empty else "No results.",
        "",
        "## Debit vs Credit",
        "",
        premium_metrics.to_markdown(index=False, floatfmt=".3f") if not premium_metrics.empty else "No results.",
        "",
        "## By Regime",
        "",
        regime_metrics.to_markdown(index=False, floatfmt=".3f") if not regime_metrics.empty else "No results.",
        "",
        "## By Controlled Surface State",
        "",
        mispricing_metrics.to_markdown(index=False, floatfmt=".3f") if not mispricing_metrics.empty else "No results.",
        "",
        "## Interpretation",
        "",
        "This tournament is the discovery stage. Promotion must be learned on one set of worlds and evaluated on different worlds; selecting winners and reporting their in-sample aggregate would be curve fitting.",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full defined-risk intraday SPY strategy tournament.")
    parser.add_argument("--worlds", type=int, default=250)
    parser.add_argument("--seed", type=int, default=20260806)
    parser.add_argument("--world-offset", type=int, default=0)
    parser.add_argument("--batch-id", type=int, default=1)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "full_strategy_tournament",
    )
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    _, weights, sector_ids, beta, idio_multiplier = create_universe(rng)

    world_rows: list[dict] = []
    trades: list[dict] = []
    for local_world in range(args.worlds):
        world_row, strategy_trades = simulate_world(
            world=local_world,
            rng=rng,
            weights=weights,
            sector_ids=sector_ids,
            beta=beta,
            idio_multiplier=idio_multiplier,
        )
        global_world = args.world_offset + local_world
        world_row["local_world"] = local_world
        world_row["world"] = global_world
        world_row["batch_id"] = args.batch_id
        world_rows.append(world_row)
        for trade in strategy_trades:
            trade["local_world"] = local_world
            trade["world"] = global_world
            trade["batch_id"] = args.batch_id
            trades.append(trade)
        if (local_world + 1) % 50 == 0:
            print(f"Completed {local_world + 1}/{args.worlds} worlds")

    worlds_frame = pd.DataFrame(world_rows)
    trades_frame = pd.DataFrame(trades)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    worlds_frame.to_csv(args.output_dir / "worlds.csv", index=False)
    trades_frame.to_csv(args.output_dir / "trades.csv", index=False)

    summary = build_summary(worlds_frame, trades_frame)
    summary["simulation"]["seed"] = int(args.seed)
    summary["simulation"]["batch_id"] = int(args.batch_id)
    summary["simulation"]["world_offset"] = int(args.world_offset)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    metric_spec = dict(
        trades=("pnl", "size"),
        win_rate=("profitable", "mean"),
        net_pnl=("pnl", "sum"),
        average_pnl=("pnl", "mean"),
        median_pnl=("pnl", "median"),
        average_edge_score=("edge_score", "mean"),
        average_max_loss=("max_loss", "mean"),
        average_return_on_risk=("return_on_risk", "mean"),
    )
    if trades_frame.empty:
        regime_metrics = pd.DataFrame()
        structure_metrics = pd.DataFrame()
        family_metrics = pd.DataFrame()
        premium_metrics = pd.DataFrame()
        mispricing_metrics = pd.DataFrame()
    else:
        trades_frame["return_on_risk"] = trades_frame["pnl"] / trades_frame["max_loss"].clip(lower=1e-9)
        trades_frame.to_csv(args.output_dir / "trades.csv", index=False)
        regime_metrics = trades_frame.groupby("regime", observed=False).agg(**metric_spec).reset_index().sort_values("net_pnl", ascending=False)
        structure_metrics = trades_frame.groupby("structure", observed=False).agg(**metric_spec).reset_index().sort_values("net_pnl", ascending=False)
        family_metrics = trades_frame.groupby("family", observed=False).agg(**metric_spec).reset_index().sort_values("net_pnl", ascending=False)
        premium_metrics = trades_frame.groupby("premium_style", observed=False).agg(**metric_spec).reset_index().sort_values("net_pnl", ascending=False)
        mispricing_metrics = trades_frame.groupby("mispricing_type", observed=False).agg(**metric_spec).reset_index().sort_values("net_pnl", ascending=False)

    regime_metrics.to_csv(args.output_dir / "regime_metrics.csv", index=False)
    structure_metrics.to_csv(args.output_dir / "structure_metrics.csv", index=False)
    family_metrics.to_csv(args.output_dir / "family_metrics.csv", index=False)
    premium_metrics.to_csv(args.output_dir / "premium_style_metrics.csv", index=False)
    mispricing_metrics.to_csv(args.output_dir / "mispricing_metrics.csv", index=False)
    calibration_table(trades_frame).to_csv(args.output_dir / "calibration.csv", index=False)
    write_report(
        args.output_dir,
        summary,
        regime_metrics,
        structure_metrics,
        family_metrics,
        premium_metrics,
        mispricing_metrics,
    )

    print(json.dumps(summary, indent=2))
    print(f"Saved results to {args.output_dir}")


if __name__ == "__main__":
    main()

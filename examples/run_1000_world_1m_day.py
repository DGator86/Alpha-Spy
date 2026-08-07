from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_spy.research.backtest.engine import summarize_expiration_backtest
from alpha_spy.research.backtest.metrics import brier_score, calibration_error, log_score

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
    legs: list[tuple[str, float, int]]  # right, strike, quantity (+ long / - short)
    entry_cashflow: float  # positive debit, negative credit
    fair_q_value: float
    physical_expected_payoff: float
    net_q_edge: float
    physical_expected_net: float
    uncertainty: float
    score: float
    max_loss: float
    predicted_probability_profit: float


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


def structure_values(
    *,
    name: str,
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
    normal_draws: np.ndarray,
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
        if quantity > 0:
            raw_entry += quantity * ask
        else:
            raw_entry += quantity * bid
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
    # Positive entry_cashflow is a debit. Negative is a net credit received.
    entry_cashflow = raw_entry + slippage + fees
    net_q_edge = fair_q - entry_cashflow
    physical_expected_net = expected_p - entry_cashflow
    uncertainty = max(
        0.025,
        uncertainty_scale * (0.025 + 0.12 * abs(fair_q) + 0.40 * abs(model_q_iv - market_iv) * spot * math.sqrt(tenor)),
    )
    score = min(net_q_edge, physical_expected_net) / uncertainty

    # Monte Carlo probability of positive terminal P&L under the physical model.
    terminal = spot * np.exp(
        (model_p_drift_annual - 0.5 * model_p_iv**2) * tenor
        + model_p_iv * math.sqrt(tenor) * normal_draws
    )
    payoffs = np.zeros_like(terminal)
    for right, strike, quantity in legs:
        intrinsic = np.maximum(terminal - strike, 0.0) if right == "C" else np.maximum(strike - terminal, 0.0)
        payoffs += quantity * intrinsic
    pnl = payoffs - entry_cashflow
    probability_profit = float(np.mean(pnl > 0.0))

    # Exact finite maximum loss for supported structures.
    probe = np.array([0.0, *[strike for _, strike, _ in legs], spot * 3.0])
    terminal_payoff = np.array([payoff_from_legs(x, legs) for x in probe])
    max_loss = max(0.0, float(entry_cashflow - terminal_payoff.min()))

    return StructureQuote(
        name=name,
        legs=legs,
        entry_cashflow=float(entry_cashflow),
        fair_q_value=float(fair_q),
        physical_expected_payoff=float(expected_p),
        net_q_edge=float(net_q_edge),
        physical_expected_net=float(physical_expected_net),
        uncertainty=float(uncertainty),
        score=float(score),
        max_loss=float(max_loss),
        predicted_probability_profit=probability_profit,
    )


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
    normal_draws: np.ndarray,
) -> tuple[dict, dict | None]:
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

    trade: dict | None = None
    max_candidate_score = -1e9
    max_candidate_q_edge = -1e9
    max_candidate_p_edge = -1e9
    max_candidate_pop = 0.0
    best_candidate_name = ""
    min_entry_minute = 35
    max_entry_minute = 350

    for minute in range(min_entry_minute, max_entry_minute + 1):
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
        direction = 1.0 if pressure_5 + 0.45 * pressure_15 >= 0 else -1.0
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
        # Keep the physical forecast realistic and bounded.
        forecast_remaining_return = float(np.clip(0.75 * forecast_remaining_return, -0.022, 0.022))
        model_p_drift_annual = forecast_remaining_return / max(tenor, 1e-9)

        atm = float(round(spot))
        width = 2.0
        expected_move = max(1.0, spot * market_iv[minute] * math.sqrt(tenor))
        body = max(1.0, round(0.72 * expected_move))
        wing = 2.0

        candidates: list[StructureQuote] = []
        surface_vol_gap = float(model_q_iv[minute] - market_iv[minute])
        surface_skew_gap = float(model_q_skew[minute] - market_skew[minute])
        directional_strength = abs(forecast_remaining_return)
        if abs(surface_vol_gap) < 0.003 and abs(surface_skew_gap) < 0.025 and directional_strength < 0.0012:
            continue

        if direction > 0:
            candidates.append(
                structure_values(
                    name="CALL_DEBIT_VERTICAL",
                    legs=[("C", atm, 1), ("C", atm + width, -1)],
                    spot=spot,
                    tenor=tenor,
                    market_iv=market_iv[minute],
                    market_skew=market_skew[minute],
                    model_q_iv=model_q_iv[minute],
                    model_q_skew=model_q_skew[minute],
                    model_p_iv=model_p_iv[minute],
                    model_p_drift_annual=model_p_drift_annual,
                    uncertainty_scale=1.00,
                    normal_draws=normal_draws,
                )
            )
        else:
            candidates.append(
                structure_values(
                    name="PUT_DEBIT_VERTICAL",
                    legs=[("P", atm, 1), ("P", atm - width, -1)],
                    spot=spot,
                    tenor=tenor,
                    market_iv=market_iv[minute],
                    market_skew=market_skew[minute],
                    model_q_iv=model_q_iv[minute],
                    model_q_skew=model_q_skew[minute],
                    model_p_iv=model_p_iv[minute],
                    model_p_drift_annual=model_p_drift_annual,
                    uncertainty_scale=1.00,
                    normal_draws=normal_draws,
                )
            )

        if surface_vol_gap > 0.004:
            candidates.append(
                structure_values(
                    name="LONG_STRADDLE",
                    legs=[("C", atm, 1), ("P", atm, 1)],
                    spot=spot,
                    tenor=tenor,
                    market_iv=market_iv[minute],
                    market_skew=market_skew[minute],
                    model_q_iv=model_q_iv[minute],
                    model_q_skew=model_q_skew[minute],
                    model_p_iv=model_p_iv[minute],
                    model_p_drift_annual=model_p_drift_annual,
                    uncertainty_scale=1.12,
                    normal_draws=normal_draws,
                )
            )

        short_put = atm - body
        long_put = short_put - wing
        short_call = atm + body
        long_call = short_call + wing
        if surface_vol_gap < -0.004 and directional_strength < 0.006:
            candidates.append(
                structure_values(
                    name="IRON_CONDOR",
                    legs=[("P", long_put, 1), ("P", short_put, -1), ("C", short_call, -1), ("C", long_call, 1)],
                    spot=spot,
                    tenor=tenor,
                    market_iv=market_iv[minute],
                    market_skew=market_skew[minute],
                    model_q_iv=model_q_iv[minute],
                    model_q_skew=model_q_skew[minute],
                    model_p_iv=model_p_iv[minute],
                    model_p_drift_annual=model_p_drift_annual,
                    uncertainty_scale=1.25,
                    normal_draws=normal_draws,
                )
            )

        best = max(candidates, key=lambda candidate: candidate.score)
        if best.score > max_candidate_score:
            max_candidate_score = best.score
            max_candidate_q_edge = best.net_q_edge
            max_candidate_p_edge = best.physical_expected_net
            max_candidate_pop = best.predicted_probability_profit
            best_candidate_name = best.name
        # Hard gates mirror the repository thesis: Q disagreement, positive P expectancy,
        # cost/uncertainty margin, and finite risk.
        if (
            best.net_q_edge >= 0.08
            and best.physical_expected_net >= 0.08
            and best.score >= 0.58
            and best.predicted_probability_profit >= 0.52
            and best.max_loss > 0.0
            and best.max_loss <= 4.50
        ):
            terminal_spot = float(spy_prices[-1])
            terminal_payoff = payoff_from_legs(terminal_spot, best.legs)
            pnl_per_share = terminal_payoff - best.entry_cashflow
            trade = {
                "world": world,
                "regime": regime.name,
                "mispricing_type": mispricing_type,
                "event_type": event_type,
                "entry_minute": minute,
                "minutes_remaining": remaining_minutes,
                "structure": best.name,
                "spot_entry": spot,
                "terminal_spot": terminal_spot,
                "market_iv": float(market_iv[minute]),
                "model_q_iv": float(model_q_iv[minute]),
                "model_p_iv": float(model_p_iv[minute]),
                "market_skew": float(market_skew[minute]),
                "model_q_skew": float(model_q_skew[minute]),
                "forecast_remaining_return": forecast_remaining_return,
                "actual_remaining_return": terminal_spot / spot - 1.0,
                "breadth_5": breadth_5,
                "concentration_5": concentration,
                "entry_cashflow": best.entry_cashflow,
                "fair_q_value": best.fair_q_value,
                "net_q_edge": best.net_q_edge,
                "physical_expected_net": best.physical_expected_net,
                "uncertainty": best.uncertainty,
                "edge_score": best.score,
                "predicted_probability_profit": best.predicted_probability_profit,
                "max_loss": best.max_loss * MULTIPLIER,
                "terminal_payoff": terminal_payoff * MULTIPLIER,
                "pnl": pnl_per_share * MULTIPLIER,
                "profitable": pnl_per_share > 0.0,
                "injected_edge_active": episode_start <= minute <= episode_end and mispricing_type != "none",
                "episode_start": episode_start,
                "episode_end": episode_end,
                "event_start": event_start,
            }
            break

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
        "trade_taken": trade is not None,
        "pnl": 0.0 if trade is None else float(trade["pnl"]),
        "max_candidate_score": float(max_candidate_score),
        "max_candidate_q_edge": float(max_candidate_q_edge),
        "max_candidate_p_edge": float(max_candidate_p_edge),
        "max_candidate_pop": float(max_candidate_pop),
        "best_candidate_name": best_candidate_name,
    }
    return world_row, trade


def calibration_table(trades: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["bin", "trades", "mean_predicted_probability", "realized_win_rate"])
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
    expiration = summarize_expiration_backtest(trades)
    if not trades.empty:
        pnl_values = trades["pnl"].to_numpy(dtype=float)
        expiration.update({
            "median_pnl": float(np.median(pnl_values)),
            "p05_pnl": float(np.quantile(pnl_values, 0.05)),
            "p95_pnl": float(np.quantile(pnl_values, 0.95)),
            "worst_trade": float(np.min(pnl_values)),
            "best_trade": float(np.max(pnl_values)),
            "expected_shortfall_5pct": float(np.mean(pnl_values[pnl_values <= np.quantile(pnl_values, 0.05)])),
        })
        boot_rng = np.random.default_rng(73186)
        boot_means = np.mean(
            boot_rng.choice(pnl_values, size=(4000, len(pnl_values)), replace=True),
            axis=1,
        )
        expiration["mean_pnl_ci95_low"] = float(np.quantile(boot_means, 0.025))
        expiration["mean_pnl_ci95_high"] = float(np.quantile(boot_means, 0.975))
    else:
        expiration.update({
            "median_pnl": 0.0,
            "p05_pnl": 0.0,
            "p95_pnl": 0.0,
            "worst_trade": 0.0,
            "best_trade": 0.0,
            "expected_shortfall_5pct": 0.0,
            "mean_pnl_ci95_low": 0.0,
            "mean_pnl_ci95_high": 0.0,
        })
    probabilities = trades["predicted_probability_profit"].to_numpy(dtype=float) if not trades.empty else np.array([])
    outcomes = trades["profitable"].astype(float).to_numpy() if not trades.empty else np.array([])
    injected_worlds = worlds[worlds["mispricing_type"] != "none"]
    no_edge_worlds = worlds[worlds["mispricing_type"] == "none"]
    injected_trades = trades[trades["mispricing_type"] != "none"] if not trades.empty else trades
    no_edge_trades = trades[trades["mispricing_type"] == "none"] if not trades.empty else trades
    active_injected_trades = trades[trades["injected_edge_active"]] if not trades.empty else trades

    return {
        "simulation": {
            "worlds": len(worlds),
            "minutes_per_world": MINUTES_PER_DAY,
            "total_simulated_minutes": int(len(worlds) * MINUTES_PER_DAY),
            "constituents": 50,
            "resolution": "1 minute",
            "holding_period": "entry to same-day expiration",
            "maximum_trades_per_world": 1,
            "policy_status": "frozen after 200-world calibration sample with seed 20260805",
            "seed": None,
        },
        "portfolio": expiration,
        "trade_rate": float(worlds["trade_taken"].mean()),
        "trades_in_injected_worlds": len(injected_trades),
        "trades_in_no_edge_worlds": len(no_edge_trades),
        "trade_rate_in_injected_worlds": float(len(injected_trades) / max(len(injected_worlds), 1)),
        "active_episode_detection_rate": float(len(active_injected_trades) / max(len(injected_worlds), 1)),
        "trades_during_active_injected_episode": len(active_injected_trades),
        "trade_rate_in_noninjected_worlds": float(len(no_edge_trades) / max(len(no_edge_worlds), 1)),
        "pnl_in_injected_worlds": float(injected_trades["pnl"].sum()) if not injected_trades.empty else 0.0,
        "pnl_in_no_edge_worlds": float(no_edge_trades["pnl"].sum()) if not no_edge_trades.empty else 0.0,
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
    mispricing_metrics: pd.DataFrame,
) -> None:
    p = summary["portfolio"]
    c = summary["calibration"]
    lines = [
        "# 1,000-World, 1-Minute-Day Monte Carlo Report",
        "",
        "## Scope",
        "",
        "- 1,000 synthetic market worlds",
        "- 390 one-minute observations per world",
        "- 390,000 total simulated market minutes",
        "- 50 synthetic S&P 500 constituents across 10 sectors",
        "- One finite-risk 0DTE position maximum per world",
        "- Entry at executable synthetic bid/ask with slippage and fees",
        "- Same-day expiration settlement",
        "",
        "> This is a functional and statistical stress test with controlled synthetic data. It is not historical evidence of live profitability.",
        "",
        "## Aggregate Results",
        "",
        f"- Trades: **{int(p['trades'])}**",
        f"- Trade rate: **{summary['trade_rate']:.1%}**",
        f"- Net P&L: **${p['net_pnl']:,.2f}**",
        f"- Average P&L per trade: **${p['average_pnl']:,.2f}**",
        f"- Win rate: **{p['win_rate']:.1%}**",
        f"- Profit factor: **{p['profit_factor']:.2f}**",
        f"- Maximum drawdown: **${p['max_drawdown']:,.2f}**",
        f"- Per-trade Sharpe: **{p.get('sharpe_per_trade', 0.0):.3f}**",
        f"- Median P&L: **${p.get('median_pnl', 0.0):,.2f}**",
        f"- 95% bootstrap interval for mean P&L: **${p.get('mean_pnl_ci95_low', 0.0):,.2f} to ${p.get('mean_pnl_ci95_high', 0.0):,.2f}**",
        f"- Worst / best trade: **${p.get('worst_trade', 0.0):,.2f} / ${p.get('best_trade', 0.0):,.2f}**",
        "",
        "## Detection and False Positives",
        "",
        f"- Trade rate in worlds assigned an injected SPY/constituent discrepancy: **{summary['trade_rate_in_injected_worlds']:.1%}**",
        f"- Detection during the actual active discrepancy window: **{summary['active_episode_detection_rate']:.1%}**",
        f"- Trade rate in worlds without a controlled injected discrepancy: **{summary['trade_rate_in_noninjected_worlds']:.1%}**",
        f"- P&L in injected-edge worlds: **${summary['pnl_in_injected_worlds']:,.2f}**",
        f"- P&L in no-injected-edge worlds: **${summary['pnl_in_no_edge_worlds']:,.2f}**",
        "",
        "## Probability Calibration",
        "",
        f"- Mean predicted probability of profit: **{c['mean_predicted_probability']:.1%}**" if c["mean_predicted_probability"] is not None else "- No trades for calibration.",
        f"- Realized win rate: **{c['realized_win_rate']:.1%}**" if c["realized_win_rate"] is not None else "",
        f"- Brier score: **{c['brier_score']:.4f}**" if c["brier_score"] is not None else "",
        f"- Log score: **{c['log_score']:.4f}**" if c["log_score"] is not None else "",
        f"- Expected calibration error: **{c['expected_calibration_error']:.4f}**" if c["expected_calibration_error"] is not None else "",
        "",
        "## Results by Regime",
        "",
        regime_metrics.to_markdown(index=False, floatfmt=".3f") if not regime_metrics.empty else "No regime results.",
        "",
        "## Results by Structure",
        "",
        structure_metrics.to_markdown(index=False, floatfmt=".3f") if not structure_metrics.empty else "No structure results.",
        "",
        "## Results by Controlled Surface State",
        "",
        mispricing_metrics.to_markdown(index=False, floatfmt=".3f") if not mispricing_metrics.empty else "No surface-state results.",
        "",
        "## Interpretation",
        "",
        "A profitable synthetic run means the detection, pricing, execution-cost, and settlement path is behaving coherently under the assumptions encoded here. It does not establish that the assumptions match the live SPY options market. The next valid step is a point-in-time historical replay using synchronized constituent, SPY, SPX, ES, sector ETF, option-chain, dividend, and membership data.",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a 1,000-world, 1-minute SPY constituent-alpha Monte Carlo day.")
    parser.add_argument("--worlds", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "monte_carlo_1000_worlds_1m",
    )
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    _, weights, sector_ids, beta, idio_multiplier = create_universe(rng)
    normal_draws = np.random.default_rng(args.seed + 1).standard_normal(768)

    world_rows: list[dict] = []
    trades: list[dict] = []
    for world in range(args.worlds):
        world_row, trade = simulate_world(
            world=world,
            rng=rng,
            weights=weights,
            sector_ids=sector_ids,
            beta=beta,
            idio_multiplier=idio_multiplier,
            normal_draws=normal_draws,
        )
        world_rows.append(world_row)
        if trade is not None:
            trades.append(trade)
        if (world + 1) % 100 == 0:
            print(f"Completed {world + 1}/{args.worlds} worlds")

    worlds_frame = pd.DataFrame(world_rows)
    trades_frame = pd.DataFrame(trades)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    worlds_frame.to_csv(args.output_dir / "worlds.csv", index=False)
    trades_frame.to_csv(args.output_dir / "trades.csv", index=False)

    summary = build_summary(worlds_frame, trades_frame)
    summary["simulation"]["seed"] = int(args.seed)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if trades_frame.empty:
        regime_metrics = pd.DataFrame()
        structure_metrics = pd.DataFrame()
        mispricing_metrics = pd.DataFrame()
    else:
        regime_metrics = (
            trades_frame.groupby("regime", observed=False)
            .agg(
                trades=("pnl", "size"),
                win_rate=("profitable", "mean"),
                net_pnl=("pnl", "sum"),
                average_pnl=("pnl", "mean"),
                average_edge_score=("edge_score", "mean"),
                average_max_loss=("max_loss", "mean"),
            )
            .reset_index()
            .sort_values("net_pnl", ascending=False)
        )
        structure_metrics = (
            trades_frame.groupby("structure", observed=False)
            .agg(
                trades=("pnl", "size"),
                win_rate=("profitable", "mean"),
                net_pnl=("pnl", "sum"),
                average_pnl=("pnl", "mean"),
                average_edge_score=("edge_score", "mean"),
                average_max_loss=("max_loss", "mean"),
            )
            .reset_index()
            .sort_values("net_pnl", ascending=False)
        )
        mispricing_metrics = (
            trades_frame.groupby("mispricing_type", observed=False)
            .agg(
                trades=("pnl", "size"),
                active_episode_entries=("injected_edge_active", "sum"),
                win_rate=("profitable", "mean"),
                net_pnl=("pnl", "sum"),
                average_pnl=("pnl", "mean"),
                average_edge_score=("edge_score", "mean"),
            )
            .reset_index()
            .sort_values("net_pnl", ascending=False)
        )

    regime_metrics.to_csv(args.output_dir / "regime_metrics.csv", index=False)
    structure_metrics.to_csv(args.output_dir / "structure_metrics.csv", index=False)
    mispricing_metrics.to_csv(args.output_dir / "mispricing_metrics.csv", index=False)
    calibration_table(trades_frame).to_csv(args.output_dir / "calibration.csv", index=False)
    write_report(args.output_dir, summary, regime_metrics, structure_metrics, mispricing_metrics)

    print(json.dumps(summary, indent=2))
    print(f"Saved results to {args.output_dir}")


if __name__ == "__main__":
    main()

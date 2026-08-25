from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

import numpy as np
from scipy.stats import norm

from .config import SuiteConfig
from .fail_safes import (
    CONDOR_MIN_WING,
    debit_width_ok,
    prefer_defined_debits,
    reject_unsafe_condors,
)
from .session_tape import structure_session_veto
from .situation import debit_premium_ok, structure_situation_veto
from .timeutil import ET, utc_iso

PREFERRED_DEBIT_WIDTHS = (2.0, 3.0)


@dataclass(frozen=True)
class Leg:
    symbol: str
    right: str
    strike: float
    side: str
    quantity: int = 1

    @property
    def sign(self) -> int:
        return 1 if self.side.startswith("buy") else -1

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "right": self.right,
            "strike": self.strike,
            "side": self.side,
            "quantity": self.quantity,
        }


def _eligible(options: list[dict[str, Any]], config: SuiteConfig) -> list[dict[str, Any]]:
    out = []
    for option in options:
        bid = float(option.get("bid") or 0.0)
        ask = float(option.get("ask") or 0.0)
        mid = float(option.get("midpoint") or 0.0)
        iv = float(option.get("iv") or 0.0)
        if ask <= 0 or mid <= 0 or iv <= 0:
            continue
        spread = ask - bid
        if spread < 0:
            continue
        relative = spread / max(mid, 0.01)
        if relative > config.strategy.max_relative_spread:
            continue
        if int(option.get("open_interest") or 0) < config.strategy.min_open_interest:
            continue
        if int(option.get("volume") or 0) < config.strategy.min_volume:
            continue
        out.append(option)
    return out


def _nearest(options: list[dict[str, Any]], strike: float, right: str) -> dict[str, Any] | None:
    rows = [o for o in options if o["right"] == right]
    return min(rows, key=lambda o: abs(float(o["strike"]) - strike), default=None)


def _next_strike(
    options: list[dict[str, Any]],
    base_strike: float,
    right: str,
    direction: int,
    max_width: float,
) -> dict[str, Any] | None:
    rows = sorted([o for o in options if o["right"] == right], key=lambda o: float(o["strike"]))
    candidates = [
        o
        for o in rows
        if (float(o["strike"]) - base_strike) * direction > 0
        and abs(float(o["strike"]) - base_strike) <= max_width
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: abs(float(o["strike"]) - base_strike))


def _strike_at_width(
    options: list[dict[str, Any]],
    base_strike: float,
    right: str,
    direction: int,
    desired_width: float,
    max_width: float,
) -> dict[str, Any] | None:
    """Pick the listed strike nearest to ``base ± desired_width``, never the $1 neighbor.

    Friday's book paid on $2–$3 debit verticals. The nearest listed strike is almost
    always $1 wide, so nearest-neighbor construction made ``prefer_defined_debits``
    a no-op and left the naked long in front.
    """
    eligible: list[dict[str, Any]] = []
    for option in options:
        if option["right"] != right:
            continue
        width = abs(float(option["strike"]) - base_strike)
        if (float(option["strike"]) - base_strike) * direction <= 0:
            continue
        if width + 1e-9 < CONDOR_MIN_WING or width - 1e-9 > max_width:
            continue
        eligible.append(option)
    if not eligible:
        return None
    target = base_strike + direction * desired_width
    return min(eligible, key=lambda option: abs(float(option["strike"]) - target))


def _leg_price(option: dict[str, Any], side: str) -> float:
    return float(option["ask"] if side.startswith("buy") else option["bid"])


def _terminal_intrinsic(right: str, strike: float, terminal: np.ndarray) -> np.ndarray:
    if right == "C":
        return np.maximum(terminal - strike, 0.0)
    return np.maximum(strike - terminal, 0.0)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed


def _expiry_datetime(expiration: str, config: SuiteConfig) -> datetime:
    expiry_date = datetime.fromisoformat(expiration).date()
    hour, minute = map(int, config.strategy.assumed_expiry_time_et.split(":"))
    return datetime.combine(expiry_date, time(hour=hour, minute=minute), tzinfo=ET)


def _tenor_years(at: datetime, expiration: str, config: SuiteConfig) -> float:
    expiry = _expiry_datetime(expiration, config)
    seconds = max(0.0, (expiry - at.astimezone(ET)).total_seconds())
    if seconds <= config.strategy.minimum_horizon_tenor_seconds:
        return 0.0
    return seconds / (365.0 * 24.0 * 60.0 * 60.0)


def _bs_price_array(
    spot: np.ndarray,
    strike: float,
    tenor: float,
    volatility: float,
    right: str,
    rate: float = 0.0,
    dividend_yield: float = 0.0,
) -> np.ndarray:
    if tenor <= 0 or volatility <= 0:
        return _terminal_intrinsic(right, strike, spot)
    s = np.maximum(np.asarray(spot, dtype=float), 1e-9)
    vol = max(float(volatility), 1e-8)
    root_t = math.sqrt(tenor)
    d1 = (
        np.log(s / strike)
        + (rate - dividend_yield + 0.5 * vol * vol) * tenor
    ) / (vol * root_t)
    d2 = d1 - vol * root_t
    if right == "C":
        return s * math.exp(-dividend_yield * tenor) * norm.cdf(d1) - strike * math.exp(-rate * tenor) * norm.cdf(d2)
    return strike * math.exp(-rate * tenor) * norm.cdf(-d2) - s * math.exp(-dividend_yield * tenor) * norm.cdf(-d1)


def _family(name: str) -> str:
    if name in {"LONG_CALL", "LONG_PUT", "CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"}:
        return "directional_long"
    if name in {"LONG_STRADDLE", "LONG_STRANGLE"}:
        return "long_vol"
    if name == "IRON_CONDOR":
        return "short_vol"
    if name in {"CALL_BUTTERFLY", "PUT_BUTTERFLY"}:
        return "range_debit"
    if name in {"BULL_PUT_CREDIT_SPREAD", "BEAR_CALL_CREDIT_SPREAD"}:
        return "directional_credit"
    return "conditional"


def _scenarios_from_frozen_distribution(
    prediction: dict[str, Any],
    *,
    key: str,
    seed: int,
    paths: int = 6000,
) -> np.ndarray | None:
    distribution = prediction.get("payload", {}).get("distribution", {})
    probabilities = np.asarray(distribution.get("probability_grid") or [], dtype=float)
    quantiles = np.asarray(distribution.get(key) or [], dtype=float)
    if probabilities.size < 5 or probabilities.size != quantiles.size:
        return None
    finite = np.isfinite(probabilities) & np.isfinite(quantiles)
    probabilities, quantiles = probabilities[finite], quantiles[finite]
    if probabilities.size < 5:
        return None
    order = np.argsort(probabilities)
    probabilities, quantiles = probabilities[order], quantiles[order]
    if np.any(np.diff(probabilities) <= 0):
        return None
    # Stratified probabilities make the reconstruction deterministic and reduce
    # Monte-Carlo noise while preserving the frozen distribution shape.
    rng = np.random.default_rng(seed)
    uniforms = (np.arange(paths, dtype=float) + rng.random(paths)) / paths
    return np.interp(uniforms, probabilities, quantiles, left=quantiles[0], right=quantiles[-1])


def _evaluate_structure(
    *,
    name: str,
    legs: list[tuple[dict[str, Any], str]],
    scenarios: np.ndarray,
    q_scenarios: np.ndarray,
    config: SuiteConfig,
    prediction: dict[str, Any],
    expiration: str,
    width: float | None = None,
) -> dict[str, Any]:
    """Evaluate a 15-minute trade without pretending the option expires in 15 minutes.

    P-measure scenarios come from the forecast. Each scenario is repriced at the
    forecast horizon with the remaining tenor using the observed option IV as the
    Q/surface anchor. Entry uses executable ask/bid. Horizon liquidation includes a
    conservative spread/slippage charge and a full round-trip fee estimate.
    """
    grouped: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
    for option, side in legs:
        key = (str(option["symbol"]), side)
        previous = grouped.get(key)
        grouped[key] = (option, (previous[1] if previous else 0) + 1)

    created_text = prediction.get("created_at")
    created_at = _parse_iso(created_text) if created_text else datetime.now(ET)
    target_text = prediction.get("target_at")
    target_at = (
        _parse_iso(target_text)
        if target_text
        else created_at + timedelta(minutes=int(prediction.get("horizon_minutes") or config.prediction.horizon_minutes))
    )
    current_tenor = _tenor_years(created_at, expiration, config)
    horizon_tenor = _tenor_years(target_at, expiration, config)
    entry_cashflow = 0.0
    horizon_cashflow = np.zeros_like(scenarios, dtype=float)
    expiration_value = np.zeros(2 + len({float(o["strike"]) for o, _ in legs}) + 1, dtype=float)
    leg_objects: list[Leg] = []
    combined_spread = 0.0
    q_net_value = 0.0
    total_contract_legs = 0

    strikes = sorted({float(option["strike"]) for option, _ in legs})
    far_upper = max([float(np.max(scenarios)) * 2.0, *(strike * 2.0 for strike in strikes), 1.0])
    payoff_points = np.asarray([0.0, *strikes, far_upper], dtype=float)
    expiration_value = np.zeros_like(payoff_points)
    net_high_slope = 0

    for (symbol, side), (option, quantity) in grouped.items():
        sign = 1 if side.startswith("buy") else -1
        bid = float(option["bid"])
        ask = float(option["ask"])
        spread = max(ask - bid, 0.0)
        iv = float(np.clip(float(option.get("iv") or 0.0), config.strategy.q_iv_floor, config.strategy.q_iv_cap))
        strike = float(option["strike"])
        right = str(option["right"])

        entry_cashflow += -sign * _leg_price(option, side) * quantity
        iv_change = float(prediction.get("payload", {}).get("forecast_iv_change") or 0.0)
        p_horizon_iv = float(np.clip(iv + iv_change, config.strategy.q_iv_floor, config.strategy.q_iv_cap))
        theoretical_horizon = _bs_price_array(scenarios, strike, horizon_tenor, p_horizon_iv, right)
        exit_slippage = max(
            config.trading.minimum_slippage,
            config.trading.slippage_fraction_of_spread * spread,
        )
        half_spread = 0.5 * spread
        if sign > 0:
            executable_horizon = np.maximum(0.0, theoretical_horizon - half_spread - exit_slippage)
            horizon_cashflow += executable_horizon * quantity
        else:
            executable_horizon = theoretical_horizon + half_spread + exit_slippage
            horizon_cashflow -= executable_horizon * quantity

        # Q valuation must come from the synthetic risk-neutral distribution, not
        # from repricing the current spot with the same market IV (which merely
        # reproduces the market surface). Preserve the contract's observed skew
        # relative to SPY ATM while replacing the level with the model-Q variance.
        dist = prediction.get("payload", {}).get("distribution", {})
        effective_minutes = max(1.0, float(prediction.get("payload", {}).get("effective_horizon_minutes") or prediction.get("horizon_minutes") or 15))
        horizon_years = effective_minutes / (252.0 * 390.0)
        q_return_sigma = max(float(dist.get("q_volatility") or 0.0), 1e-8)
        q_annual_iv = q_return_sigma / math.sqrt(max(horizon_years, 1e-12))
        spy_iv = prediction.get("payload", {}).get("surface_snapshot", {}).get("spy_iv")
        skew_ratio = iv / max(float(spy_iv or iv), config.strategy.q_iv_floor)
        q_horizon_iv = float(np.clip(q_annual_iv * skew_ratio, config.strategy.q_iv_floor, config.strategy.q_iv_cap))
        q_horizon_value = _bs_price_array(q_scenarios, strike, horizon_tenor, q_horizon_iv, right)
        q_net_value += sign * float(np.mean(q_horizon_value)) * quantity
        expiration_value += sign * quantity * _terminal_intrinsic(right, strike, payoff_points)
        combined_spread += spread * quantity
        total_contract_legs += quantity
        if right == "C":
            net_high_slope += sign * quantity
        leg_objects.append(
            Leg(symbol=symbol, right=right, strike=strike, side=side, quantity=quantity)
        )

    roundtrip_fees_dollars = 2.0 * total_contract_legs * config.trading.fee_per_contract
    pnl_dollars = (entry_cashflow + horizon_cashflow) * 100.0 - roundtrip_fees_dollars
    expected_value = float(np.mean(pnl_dollars))
    probability_profit = float(np.mean(pnl_dollars > 0.0))

    deterministic_pnl = (entry_cashflow + expiration_value) * 100.0 - roundtrip_fees_dollars
    theoretical_min = float(np.min(deterministic_pnl))
    theoretical_max = float(np.max(deterministic_pnl))
    profit_unbounded = net_high_slope > 0
    max_profit = (
        max(0.0, float(np.quantile(pnl_dollars, 0.995)))
        if profit_unbounded
        else max(0.0, theoretical_max)
    )
    max_loss = max(0.0, -theoretical_min)
    entry_kind = "credit" if entry_cashflow > 0 else "debit"
    entry_price = abs(entry_cashflow)

    q_structure_value = q_net_value
    # entry_cashflow is +credit / -debit. q_net_value is the model-Q value of
    # the opened position. Their sum is the executable Q edge at inception.
    q_executable_edge = (entry_cashflow + q_net_value) * 100.0
    pnl_std = float(np.std(pnl_dollars, ddof=1)) if len(pnl_dollars) > 1 else 0.0
    payload = prediction.get("payload", {})
    model_uncertainty = float(payload.get("model_uncertainty") or 0.0)
    sigma_return = max(float(prediction.get("sigma_return") or 0.0), 1e-6)
    uncertainty_ratio = model_uncertainty / sigma_return
    edge_to_uncertainty = expected_value / max(pnl_std * (0.25 + uncertainty_ratio), 1.0)
    extra_cost_dollars = (
        roundtrip_fees_dollars
        + combined_spread * 100.0 * max(config.trading.slippage_fraction_of_spread, 0.0)
    )
    doubled_cost_expected_value = expected_value - extra_cost_dollars

    family = _family(name)
    path = payload.get("path", {})
    distribution = payload.get("distribution", {})
    regime = payload.get("regime_state", {})
    consensus = payload.get("multi_horizon_consensus", {})
    probability_up = float(prediction.get("probability_up") or 0.5)
    p_vol = float(distribution.get("p_volatility") or 0.0)
    q_vol = float(distribution.get("q_volatility") or 0.0)
    archetype = str(path.get("archetype") or "mixed_path")
    strategy_fit = True
    fit_reasons: list[str] = []
    if (
        config.strategy.require_multi_horizon_alignment
        and family in {"directional_long", "directional_credit"}
        and not bool(consensus.get("aligned"))
    ):
        strategy_fit = False
        fit_reasons.append("multi_horizon_not_aligned")
    if family in {"directional_long", "directional_credit"}:
        bullish = name in {"LONG_CALL", "CALL_DEBIT_SPREAD", "BULL_PUT_CREDIT_SPREAD"}
        bearish = name in {"LONG_PUT", "PUT_DEBIT_SPREAD", "BEAR_CALL_CREDIT_SPREAD"}
        if bullish and probability_up < 0.55:
            strategy_fit = False
            fit_reasons.append("bullish_probability_too_low")
        if bearish and probability_up > 0.45:
            strategy_fit = False
            fit_reasons.append("bearish_probability_too_low")
        if family == "directional_credit":
            strategy_fit = False
            fit_reasons.append("directional_credits_disabled_tape_policy")
        if name in {"LONG_CALL", "LONG_PUT"}:
            strategy_fit = False
            fit_reasons.append("naked_long_disabled_tape_policy")
        if name in {"CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"} and not debit_width_ok(width):
            strategy_fit = False
            fit_reasons.append("debit_width_outside_preferred")
    elif family == "short_vol":
        if q_vol <= p_vol * 1.03:
            strategy_fit = False
            fit_reasons.append("vol_not_rich_enough")
        if archetype not in {"compression_range", "two_sided_mean_reversion", "mixed_path"}:
            strategy_fit = False
            fit_reasons.append("path_not_range_friendly")
        if regime.get("dealer_gamma") == "negative_gamma" or regime.get("volatility") == "crisis":
            strategy_fit = False
            fit_reasons.append("short_vol_tail_regime")
    elif family == "long_vol":
        convex_probability = max(
            float(path.get("probability_squeeze_up") or 0.0),
            float(path.get("probability_liquidation_down") or 0.0),
        )
        if not (p_vol >= q_vol * 1.01 or convex_probability >= 0.18 or archetype == "two_sided_mean_reversion"):
            strategy_fit = False
            fit_reasons.append("long_vol_edge_not_confirmed")
    elif family == "range_debit" and archetype not in {"compression_range", "two_sided_mean_reversion", "mixed_path"}:
        strategy_fit = False
        fit_reasons.append("range_debit_path_mismatch")

    signals = (payload.get("market_context") or {}).get("signals") or {}
    if isinstance(signals, dict):
        veto = structure_session_veto(
            name,
            family,
            open_bps=signals.get("session_open_distance_bps"),
            vwap_bps=signals.get("auction_vwap_distance_bps"),
        )
        if veto:
            strategy_fit = False
            fit_reasons.append(veto)
        situation_veto = structure_situation_veto(
            name,
            family,
            situation=str(signals.get("situation") or "UNKNOWN"),
            direction=int(signals.get("situation_direction") or 0),
        )
        if situation_veto:
            strategy_fit = False
            fit_reasons.append(situation_veto)
    if name in {"CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"} and not debit_premium_ok(entry_price, width):
        strategy_fit = False
        fit_reasons.append("debit_premium_too_thin")
    score = expected_value / max(max_loss, 1.0) + 0.25 * (probability_profit - 0.5)
    if pnl_std > 1e-9:
        score += 0.05 * expected_value / pnl_std
        score -= 0.10 * uncertainty_ratio
        score += 0.05 if strategy_fit else -0.25
        score += 0.05 * q_executable_edge / max(max_loss, 1.0)
        if name in {"CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"}:
            structure_width = float(width or 0.0)
            if 2.0 <= structure_width <= 3.5:
                score += 0.08
            elif structure_width > 5.0:
                score -= 0.06
        elif name in {"LONG_CALL", "LONG_PUT"}:
            score -= 0.04

    accepted = (
        expected_value >= config.strategy.min_edge_dollars * 100.0
        and probability_profit >= config.strategy.min_probability
        and edge_to_uncertainty >= config.strategy.min_edge_to_uncertainty
        and max_loss <= max(config.risk.maximum_trade_risk_dollars, 1.0)
        and (entry_kind != "credit" or entry_price >= config.strategy.min_credit)
        and (entry_kind != "debit" or entry_price <= config.strategy.max_debit)
        and strategy_fit
        and (not config.strategy.require_positive_doubled_cost_ev or doubled_cost_expected_value > 0.0)
    )
    reasons = []
    if expected_value < config.strategy.min_edge_dollars * 100.0:
        reasons.append("edge_below_threshold")
    if probability_profit < config.strategy.min_probability:
        reasons.append("probability_below_threshold")
    if edge_to_uncertainty < config.strategy.min_edge_to_uncertainty:
        reasons.append("edge_to_uncertainty_below_threshold")
    if max_loss > config.risk.maximum_trade_risk_dollars:
        reasons.append("risk_exceeds_cap")
    if entry_kind == "credit" and entry_price < config.strategy.min_credit:
        reasons.append("credit_too_small")
    if entry_kind == "debit" and entry_price > config.strategy.max_debit:
        reasons.append("debit_too_large")
    if config.strategy.require_positive_doubled_cost_ev and doubled_cost_expected_value <= 0.0:
        reasons.append("fails_doubled_cost_ev")
    reasons.extend(fit_reasons)

    return {
        "candidate_id": f"C-{uuid.uuid4().hex[:16]}",
        "prediction_id": prediction["prediction_id"],
        "created_at": utc_iso(),
        "strategy": name,
        "status": "ELIGIBLE" if accepted else "REJECTED",
        "score": float(score),
        "probability_profit": probability_profit,
        "expected_value": expected_value,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "entry_price": entry_price,
        "entry_kind": entry_kind,
        "width": width,
        "expiration": expiration,
        "rejection_reason": None if accepted else ",".join(reasons) or "rejected",
        "legs": [leg.as_dict() for leg in leg_objects],
        "payload": {
            "family": family,
            "valuation_method": "P-Q-horizon-reprice-cost-stress-v3.0",
            "current_tenor_years": current_tenor,
            "horizon_tenor_years": horizon_tenor,
            "q_structure_value": q_structure_value,
            "q_executable_edge": q_executable_edge,
            "p_distribution_source": distribution.get("p_source"),
            "q_distribution_source": distribution.get("q_source"),
            "p_scenario_source": "frozen_distribution_quantiles",
            "q_scenario_source": "frozen_distribution_quantiles",
            "p_expected_pnl": expected_value,
            "p_pnl_std": pnl_std,
            "model_uncertainty": model_uncertainty,
            "uncertainty_ratio": uncertainty_ratio,
            "edge_to_uncertainty": edge_to_uncertainty,
            "doubled_cost_expected_value": doubled_cost_expected_value,
            "strategy_fit": strategy_fit,
            "strategy_fit_reasons": fit_reasons,
            "roundtrip_fees_dollars": roundtrip_fees_dollars,
            "combined_spread": combined_spread,
            "scenario_p05": float(np.quantile(pnl_dollars, 0.05)),
            "scenario_p50": float(np.quantile(pnl_dollars, 0.50)),
            "scenario_p95": float(np.quantile(pnl_dollars, 0.95)),
            "theoretical_min": theoretical_min,
            "theoretical_max": theoretical_max,
            "profit_unbounded": profit_unbounded,
            "entry_kind": entry_kind,
            "surface_snapshot": prediction.get("payload", {}).get("surface_snapshot", {}),
            "regime_state": prediction.get("payload", {}).get("regime_state", {}),
            "forecast_expected_return": prediction.get("expected_return"),
            "forecast_sigma_return": prediction.get("sigma_return"),
            "entry_spot": prediction.get("spy_price"),
            "forecast_horizon_minutes": prediction.get("horizon_minutes"),
        },
    }


def generate_candidates(
    config: SuiteConfig,
    prediction: dict[str, Any],
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    options = _eligible(options, config)
    if not options:
        return []

    input_health = prediction.get("payload", {}).get("input_health", {})
    if input_health.get("required_ok") is False:
        return []

    seed = int(prediction["feature_hash"][:8], 16)
    scenarios = _scenarios_from_frozen_distribution(
        prediction, key="p_price_quantiles", seed=seed, paths=6000
    )
    q_scenarios = _scenarios_from_frozen_distribution(
        prediction, key="q_price_quantiles", seed=seed ^ 0xA5A5A5A5, paths=6000
    )
    if scenarios is None:
        rng = np.random.default_rng(seed)
        mu = math.log(float(prediction["spy_price"])) + float(prediction["expected_return"])
        sigma = max(float(prediction["sigma_return"]), 1e-6)
        z = rng.standard_t(df=7.0, size=6000) * math.sqrt((7.0 - 2.0) / 7.0)
        scenarios = np.exp(mu + sigma * z)
    if q_scenarios is None:
        q_scenarios = np.asarray(scenarios, dtype=float).copy()
    spot = float(prediction["spy_price"])
    target = float(prediction["predicted_price"])
    low = float(prediction["predicted_low"])
    high = float(prediction["predicted_high"])
    expiration = str(options[0].get("expiration") or "")

    enabled = set(config.strategy.enabled_families)
    candidates: list[dict[str, Any]] = []

    def add(name: str, legs: list[tuple[dict[str, Any], str]], width: float | None = None) -> None:
        if name not in enabled or not legs or any(option is None for option, _ in legs):
            return
        try:
            candidates.append(
                _evaluate_structure(
                    name=name,
                    legs=legs,
                    scenarios=scenarios,
                    q_scenarios=q_scenarios,
                    config=config,
                    prediction=prediction,
                    expiration=expiration,
                    width=width,
                )
            )
        except Exception:
            return

    atm_call = _nearest(options, spot, "C")
    atm_put = _nearest(options, spot, "P")
    target_call = _nearest(options, target, "C")
    target_put = _nearest(options, target, "P")
    low_put = _nearest(options, low, "P")
    high_call = _nearest(options, high, "C")

    if target_call:
        add("LONG_CALL", [(target_call, "buy_to_open")])
        seen_call_widths: set[float] = set()
        for desired in PREFERRED_DEBIT_WIDTHS:
            upper = _strike_at_width(
                options,
                float(target_call["strike"]),
                "C",
                1,
                desired,
                config.strategy.max_width,
            )
            if not upper:
                continue
            width = float(upper["strike"]) - float(target_call["strike"])
            if round(width, 2) in seen_call_widths or not debit_width_ok(width):
                continue
            seen_call_widths.add(round(width, 2))
            add(
                "CALL_DEBIT_SPREAD",
                [(target_call, "buy_to_open"), (upper, "sell_to_open")],
                width=width,
            )
    if target_put:
        add("LONG_PUT", [(target_put, "buy_to_open")])
        seen_put_widths: set[float] = set()
        for desired in PREFERRED_DEBIT_WIDTHS:
            lower = _strike_at_width(
                options,
                float(target_put["strike"]),
                "P",
                -1,
                desired,
                config.strategy.max_width,
            )
            if not lower:
                continue
            width = float(target_put["strike"]) - float(lower["strike"])
            if round(width, 2) in seen_put_widths or not debit_width_ok(width):
                continue
            seen_put_widths.add(round(width, 2))
            add(
                "PUT_DEBIT_SPREAD",
                [(target_put, "buy_to_open"), (lower, "sell_to_open")],
                width=width,
            )

    if low_put:
        hedge = _next_strike(options, float(low_put["strike"]), "P", -1, config.strategy.max_width)
        if hedge:
            add(
                "BULL_PUT_CREDIT_SPREAD",
                [(low_put, "sell_to_open"), (hedge, "buy_to_open")],
                width=float(low_put["strike"]) - float(hedge["strike"]),
            )
    if high_call:
        hedge = _next_strike(options, float(high_call["strike"]), "C", 1, config.strategy.max_width)
        if hedge:
            add(
                "BEAR_CALL_CREDIT_SPREAD",
                [(high_call, "sell_to_open"), (hedge, "buy_to_open")],
                width=float(hedge["strike"]) - float(high_call["strike"]),
            )

    if atm_call and atm_put:
        add("LONG_STRADDLE", [(atm_call, "buy_to_open"), (atm_put, "buy_to_open")])
    strangle_call = _nearest(options, high, "C")
    strangle_put = _nearest(options, low, "P")
    if strangle_call and strangle_put:
        add("LONG_STRANGLE", [(strangle_call, "buy_to_open"), (strangle_put, "buy_to_open")])

    if low_put and high_call:
        long_put = _strike_at_width(
            options,
            float(low_put["strike"]),
            "P",
            -1,
            CONDOR_MIN_WING,
            config.strategy.max_width,
        )
        long_call = _strike_at_width(
            options,
            float(high_call["strike"]),
            "C",
            1,
            CONDOR_MIN_WING,
            config.strategy.max_width,
        )
        if long_put and long_call:
            add(
                "IRON_CONDOR",
                [
                    (long_put, "buy_to_open"),
                    (low_put, "sell_to_open"),
                    (high_call, "sell_to_open"),
                    (long_call, "buy_to_open"),
                ],
                width=max(
                    float(low_put["strike"]) - float(long_put["strike"]),
                    float(long_call["strike"]) - float(high_call["strike"]),
                ),
            )

    for right_code, name, center in (
        ("C", "CALL_BUTTERFLY", target),
        ("P", "PUT_BUTTERFLY", target),
    ):
        middle = _nearest(options, center, right_code)
        if middle:
            lower = _next_strike(
                options, float(middle["strike"]), right_code, -1, config.strategy.max_width
            )
            upper = _next_strike(
                options, float(middle["strike"]), right_code, 1, config.strategy.max_width
            )
            if lower and upper:
                left = float(middle["strike"]) - float(lower["strike"])
                right_width = float(upper["strike"]) - float(middle["strike"])
                if abs(left - right_width) <= 0.51:
                    add(
                        name,
                        [
                            (lower, "buy_to_open"),
                            (middle, "sell_to_open"),
                            (middle, "sell_to_open"),
                            (upper, "buy_to_open"),
                        ],
                        width=max(left, right_width),
                    )

    prefer_defined_debits(candidates)
    reject_unsafe_condors(candidates, spot)
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[: config.strategy.max_candidates_per_cycle]

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import SuiteConfig
from .timeutil import utc_iso


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
        if ask <= 0 or mid <= 0:
            continue
        spread = ask - bid
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
        o for o in rows
        if (float(o["strike"]) - base_strike) * direction > 0
        and abs(float(o["strike"]) - base_strike) <= max_width
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: abs(float(o["strike"]) - base_strike))


def _leg_price(option: dict[str, Any], side: str) -> float:
    return float(option["ask"] if side.startswith("buy") else option["bid"])


def _terminal_intrinsic(right: str, strike: float, terminal: np.ndarray) -> np.ndarray:
    if right == "C":
        return np.maximum(terminal - strike, 0.0)
    return np.maximum(strike - terminal, 0.0)


def _evaluate_structure(
    *,
    name: str,
    legs: list[tuple[dict[str, Any], str]],
    scenarios: np.ndarray,
    config: SuiteConfig,
    prediction_id: str,
    expiration: str,
    width: float | None = None,
) -> dict[str, Any]:
    entry_cash = 0.0
    terminal_value = np.zeros_like(scenarios)
    leg_objects: list[Leg] = []
    combined_spread = 0.0

    grouped: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
    for option, side in legs:
        key = (str(option["symbol"]), side)
        previous = grouped.get(key)
        grouped[key] = (option, (previous[1] if previous else 0) + 1)

    total_contract_legs = 0
    for (symbol, side), (option, quantity) in grouped.items():
        sign = 1 if side.startswith("buy") else -1
        entry_cash += -sign * _leg_price(option, side) * quantity
        terminal_value += (
            sign
            * quantity
            * _terminal_intrinsic(option["right"], float(option["strike"]), scenarios)
        )
        combined_spread += max(float(option["ask"]) - float(option["bid"]), 0.0) * quantity
        total_contract_legs += quantity
        leg_objects.append(
            Leg(
                symbol=symbol,
                right=option["right"],
                strike=float(option["strike"]),
                side=side,
                quantity=quantity,
            )
        )

    fees = total_contract_legs * 0.65 / 100.0
    pnl_per_share = entry_cash + terminal_value - fees
    expected_value = float(np.mean(pnl_per_share) * 100.0)
    probability_profit = float(np.mean(pnl_per_share > 0.0))

    # Option payoffs are piecewise linear. Evaluating zero, every strike, and a far
    # upper boundary gives an exact bounded-risk loss for all supported structures.
    strikes = sorted({float(leg.strike) for leg in leg_objects})
    far_upper = max([float(np.max(scenarios)) * 2.0, *(strike * 2.0 for strike in strikes), 1.0])
    payoff_points = np.asarray([0.0, *strikes, far_upper], dtype=float)
    deterministic_value = np.zeros_like(payoff_points)
    net_high_slope = 0
    for leg in leg_objects:
        deterministic_value += (
            leg.sign
            * leg.quantity
            * _terminal_intrinsic(leg.right, leg.strike, payoff_points)
        )
        if leg.right == "C":
            net_high_slope += leg.sign * leg.quantity
    deterministic_pnl = entry_cash + deterministic_value - fees
    theoretical_min = float(np.min(deterministic_pnl) * 100.0)
    theoretical_max = float(np.max(deterministic_pnl) * 100.0)
    profit_unbounded = net_high_slope > 0
    max_profit = (
        max(0.0, float(np.quantile(pnl_per_share, 0.995) * 100.0))
        if profit_unbounded
        else max(0.0, theoretical_max)
    )
    max_loss = max(0.0, -theoretical_min)
    entry_kind = "credit" if entry_cash > 0 else "debit"
    entry_price = abs(entry_cash)
    liquidity_penalty = combined_spread * 100.0 * 0.25
    conservative_ev = expected_value - liquidity_penalty
    score = conservative_ev / max(max_loss, 1.0) + 0.25 * (probability_profit - 0.5)

    accepted = (
        conservative_ev >= config.strategy.min_edge_dollars * 100.0
        and probability_profit >= config.strategy.min_probability
        and max_loss <= max(config.risk.maximum_trade_risk_dollars, 1.0)
        and (entry_kind != "credit" or entry_price >= config.strategy.min_credit)
        and (entry_kind != "debit" or entry_price <= config.strategy.max_debit)
    )
    reason = None
    if not accepted:
        reasons = []
        if conservative_ev < config.strategy.min_edge_dollars * 100.0:
            reasons.append("edge_below_threshold")
        if probability_profit < config.strategy.min_probability:
            reasons.append("probability_below_threshold")
        if max_loss > config.risk.maximum_trade_risk_dollars:
            reasons.append("risk_exceeds_cap")
        if entry_kind == "credit" and entry_price < config.strategy.min_credit:
            reasons.append("credit_too_small")
        if entry_kind == "debit" and entry_price > config.strategy.max_debit:
            reasons.append("debit_too_large")
        reason = ",".join(reasons) or "rejected"

    return {
        "candidate_id": f"C-{uuid.uuid4().hex[:16]}",
        "prediction_id": prediction_id,
        "created_at": utc_iso(),
        "strategy": name,
        "status": "ELIGIBLE" if accepted else "REJECTED",
        "score": float(score),
        "probability_profit": probability_profit,
        "expected_value": conservative_ev,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "entry_price": entry_price,
        "entry_kind": entry_kind,
        "width": width,
        "expiration": expiration,
        "rejection_reason": reason,
        "legs": [leg.as_dict() for leg in leg_objects],
        "payload": {
            "raw_expected_value": expected_value,
            "liquidity_penalty": liquidity_penalty,
            "combined_spread": combined_spread,
            "scenario_p05": float(np.quantile(pnl_per_share, 0.05) * 100.0),
            "scenario_p50": float(np.quantile(pnl_per_share, 0.50) * 100.0),
            "scenario_p95": float(np.quantile(pnl_per_share, 0.95) * 100.0),
            "theoretical_min": theoretical_min,
            "theoretical_max": theoretical_max,
            "profit_unbounded": profit_unbounded,
            "entry_kind": entry_kind,
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

    seed = int(prediction["feature_hash"][:8], 16)
    rng = np.random.default_rng(seed)
    mu = math.log(float(prediction["spy_price"])) + float(prediction["expected_return"])
    sigma = max(float(prediction["sigma_return"]), 1e-6)
    scenarios = np.exp(rng.normal(mu, sigma, size=6000))
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
                    config=config,
                    prediction_id=prediction["prediction_id"],
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
        upper = _next_strike(options, float(target_call["strike"]), "C", 1, config.strategy.max_width)
        if upper:
            add(
                "CALL_DEBIT_SPREAD",
                [(target_call, "buy_to_open"), (upper, "sell_to_open")],
                width=float(upper["strike"]) - float(target_call["strike"]),
            )
    if target_put:
        add("LONG_PUT", [(target_put, "buy_to_open")])
        lower = _next_strike(options, float(target_put["strike"]), "P", -1, config.strategy.max_width)
        if lower:
            add(
                "PUT_DEBIT_SPREAD",
                [(target_put, "buy_to_open"), (lower, "sell_to_open")],
                width=float(target_put["strike"]) - float(lower["strike"]),
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
        long_put = _next_strike(options, float(low_put["strike"]), "P", -1, config.strategy.max_width)
        long_call = _next_strike(options, float(high_call["strike"]), "C", 1, config.strategy.max_width)
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

    for right, name, center in (("C", "CALL_BUTTERFLY", target), ("P", "PUT_BUTTERFLY", target)):
        middle = _nearest(options, center, right)
        if middle:
            lower = _next_strike(options, float(middle["strike"]), right, -1, config.strategy.max_width)
            upper = _next_strike(options, float(middle["strike"]), right, 1, config.strategy.max_width)
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

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[: config.strategy.max_candidates_per_cycle]

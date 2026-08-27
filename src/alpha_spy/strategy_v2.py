from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Any

import numpy as np

from .config import SuiteConfig
from .strategy import _evaluate_structure, _scenarios_from_frozen_distribution


@dataclass(frozen=True)
class V2OptimizerConfig:
    max_absolute_spread: float = 0.08
    max_relative_spread: float = 0.18
    preferred_absolute_spread: float = 0.02
    min_open_interest: int = 25
    min_volume: int = 1
    max_strike_distance: float = 18.0
    max_width: float = 10.0
    liquid_contracts_per_right: int = 24
    max_cheap_screen_structures: int = 2500
    max_full_valuations: int = 700
    min_expected_value_dollars: float = 3.0
    min_probability_profit: float = 0.55
    max_loss_dollars: float = 100.0
    estimated_pass_through_fee_per_contract_side: float = 0.03
    entry_mid_capture_fraction: float = 0.65
    exit_mid_capture_fraction: float = 0.50
    max_execution_drag_fraction_of_risk: float = 0.20
    min_edge_to_execution_drag: float = 1.25
    leg_penalty_dollars: float = 0.20
    prefer_fewer_legs_tiebreak: bool = True


@dataclass(frozen=True)
class StructureSpec:
    name: str
    legs: tuple[tuple[dict[str, Any], str, int], ...]
    width: float | None = None

    @property
    def leg_contracts(self) -> int:
        return sum(max(1, int(quantity)) for _, _, quantity in self.legs)

    @property
    def unique_legs(self) -> int:
        return len(self.legs)


def _mid(option: dict[str, Any]) -> float:
    bid = float(option.get("bid") or 0.0)
    ask = float(option.get("ask") or 0.0)
    midpoint = option.get("midpoint")
    if midpoint not in (None, ""):
        try:
            value = float(midpoint)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0.5 * (bid + ask)


def _spread(option: dict[str, Any]) -> float:
    return max(0.0, float(option.get("ask") or 0.0) - float(option.get("bid") or 0.0))


def _liquidity_score(option: dict[str, Any], spot: float, cfg: V2OptimizerConfig) -> float:
    mid = max(_mid(option), 0.01)
    spread = _spread(option)
    relative = spread / mid
    oi = max(0, int(option.get("open_interest") or 0))
    volume = max(0, int(option.get("volume") or 0))
    distance = abs(float(option.get("strike") or 0.0) - spot)
    spread_term = 1.0 / (1.0 + 14.0 * relative + 20.0 * spread)
    depth_term = math.log1p(oi) + 0.5 * math.log1p(volume)
    distance_term = 1.0 / (1.0 + distance / max(cfg.max_strike_distance, 1.0))
    penny_bonus = 0.35 if spread <= cfg.preferred_absolute_spread + 1e-9 else 0.0
    return spread_term * (1.0 + 0.08 * depth_term) * distance_term + penny_bonus


def liquid_contract_pool(
    options: list[dict[str, Any]],
    spot: float,
    cfg: V2OptimizerConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg or V2OptimizerConfig()
    eligible: list[dict[str, Any]] = []
    for option in options:
        bid = float(option.get("bid") or 0.0)
        ask = float(option.get("ask") or 0.0)
        mid = _mid(option)
        if bid <= 0 or ask <= bid or mid <= 0:
            continue
        spread = ask - bid
        relative = spread / max(mid, 0.01)
        strike = float(option.get("strike") or 0.0)
        if spread > cfg.max_absolute_spread or relative > cfg.max_relative_spread:
            continue
        if abs(strike - spot) > cfg.max_strike_distance:
            continue
        if int(option.get("open_interest") or 0) < cfg.min_open_interest:
            continue
        if int(option.get("volume") or 0) < cfg.min_volume:
            continue
        if str(option.get("right") or "") not in {"C", "P"}:
            continue
        copy = dict(option)
        copy["v2_liquidity_score"] = _liquidity_score(copy, spot, cfg)
        copy["v2_absolute_spread"] = spread
        copy["v2_relative_spread"] = relative
        eligible.append(copy)

    selected: list[dict[str, Any]] = []
    for right in ("C", "P"):
        rows = [row for row in eligible if row["right"] == right]
        rows.sort(
            key=lambda row: (
                -float(row["v2_liquidity_score"]),
                abs(float(row["strike"]) - spot),
            )
        )
        selected.extend(rows[: cfg.liquid_contracts_per_right])
    return sorted(selected, key=lambda row: (row["right"], float(row["strike"])))


def _find(options: list[dict[str, Any]], right: str, strike: float) -> dict[str, Any] | None:
    rows = [row for row in options if row["right"] == right]
    return min(rows, key=lambda row: abs(float(row["strike"]) - strike), default=None)


def _leg(option: dict[str, Any], side: str, quantity: int = 1) -> tuple[dict[str, Any], str, int]:
    return option, side, quantity


def _legacy_legs(spec: StructureSpec) -> list[tuple[dict[str, Any], str]]:
    out: list[tuple[dict[str, Any], str]] = []
    for option, side, quantity in spec.legs:
        out.extend((option, side) for _ in range(max(1, quantity)))
    return out


def _combo_quotes(spec: StructureSpec) -> dict[str, float]:
    mid_cashflow = 0.0
    natural_cashflow = 0.0
    combined_spread = 0.0
    for option, side, quantity in spec.legs:
        sign = 1 if side.startswith("buy") else -1
        mid = _mid(option)
        natural = float(option["ask"] if sign > 0 else option["bid"])
        mid_cashflow += -sign * mid * quantity
        natural_cashflow += -sign * natural * quantity
        combined_spread += _spread(option) * quantity
    return {
        "mid_cashflow": mid_cashflow,
        "natural_cashflow": natural_cashflow,
        "combined_spread": combined_spread,
    }


def _cheap_screen_score(spec: StructureSpec, spot: float, cfg: V2OptimizerConfig) -> float:
    quotes = _combo_quotes(spec)
    spread = quotes["combined_spread"]
    fee_points = 2.0 * spec.leg_contracts * cfg.estimated_pass_through_fee_per_contract_side / 100.0
    drag_points = (
        0.5 * spread * (2.0 - cfg.entry_mid_capture_fraction - cfg.exit_mid_capture_fraction)
        + fee_points
    )
    liquidity = sum(
        float(option.get("v2_liquidity_score") or 0.0) * qty
        for option, _, qty in spec.legs
    )
    distance = sum(abs(float(option["strike"]) - spot) * qty for option, _, qty in spec.legs)
    return liquidity - 8.0 * drag_points - 0.01 * distance - 0.05 * spec.leg_contracts


def enumerate_bounded_risk_specs(
    options: list[dict[str, Any]],
    spot: float,
    cfg: V2OptimizerConfig | None = None,
) -> list[StructureSpec]:
    cfg = cfg or V2OptimizerConfig()
    calls = sorted([o for o in options if o["right"] == "C"], key=lambda o: float(o["strike"]))
    puts = sorted([o for o in options if o["right"] == "P"], key=lambda o: float(o["strike"]))
    specs: list[StructureSpec] = []

    for row in calls:
        specs.append(StructureSpec("LONG_CALL", (_leg(row, "buy_to_open"),)))
    for row in puts:
        specs.append(StructureSpec("LONG_PUT", (_leg(row, "buy_to_open"),)))

    for rows, right in ((calls, "C"), (puts, "P")):
        for low, high in combinations(rows, 2):
            width = float(high["strike"]) - float(low["strike"])
            if width <= 0 or width > cfg.max_width:
                continue
            if right == "C":
                specs.append(StructureSpec("BULL_CALL_DEBIT_SPREAD", (_leg(low, "buy_to_open"), _leg(high, "sell_to_open")), width))
                specs.append(StructureSpec("BEAR_CALL_CREDIT_SPREAD", (_leg(low, "sell_to_open"), _leg(high, "buy_to_open")), width))
                specs.append(StructureSpec("CALL_BACKSPREAD_1x2", (_leg(low, "sell_to_open"), _leg(high, "buy_to_open", 2)), width))
                specs.append(StructureSpec("CALL_BACKSPREAD_1x3", (_leg(low, "sell_to_open"), _leg(high, "buy_to_open", 3)), width))
            else:
                specs.append(StructureSpec("BEAR_PUT_DEBIT_SPREAD", (_leg(high, "buy_to_open"), _leg(low, "sell_to_open")), width))
                specs.append(StructureSpec("BULL_PUT_CREDIT_SPREAD", (_leg(high, "sell_to_open"), _leg(low, "buy_to_open")), width))
                specs.append(StructureSpec("PUT_BACKSPREAD_1x2", (_leg(high, "sell_to_open"), _leg(low, "buy_to_open", 2)), width))
                specs.append(StructureSpec("PUT_BACKSPREAD_1x3", (_leg(high, "sell_to_open"), _leg(low, "buy_to_open", 3)), width))

    atm_call = _find(calls, "C", spot) if calls else None
    atm_put = _find(puts, "P", spot) if puts else None
    if atm_call and atm_put:
        specs.extend(
            [
                StructureSpec("LONG_STRADDLE", (_leg(atm_call, "buy_to_open"), _leg(atm_put, "buy_to_open"))),
                StructureSpec("LONG_STRAP", (_leg(atm_call, "buy_to_open", 2), _leg(atm_put, "buy_to_open"))),
                StructureSpec("LONG_STRIP", (_leg(atm_call, "buy_to_open"), _leg(atm_put, "buy_to_open", 2))),
            ]
        )

    otm_calls = [o for o in calls if float(o["strike"]) >= spot]
    otm_puts = [o for o in puts if float(o["strike"]) <= spot]
    itm_calls = [o for o in calls if float(o["strike"]) <= spot]
    itm_puts = [o for o in puts if float(o["strike"]) >= spot]
    for put in otm_puts[-6:]:
        for call in otm_calls[:6]:
            specs.append(StructureSpec("LONG_STRANGLE", (_leg(put, "buy_to_open"), _leg(call, "buy_to_open"))))
    for call in itm_calls[-4:]:
        for put in itm_puts[:4]:
            specs.append(StructureSpec("LONG_GUTS", (_leg(call, "buy_to_open"), _leg(put, "buy_to_open"))))

    for rows, prefix in ((calls, "CALL"), (puts, "PUT")):
        for i in range(len(rows) - 2):
            for j in range(i + 1, min(i + 5, len(rows) - 1)):
                for k in range(j + 1, min(j + 5, len(rows))):
                    a, b, c = rows[i], rows[j], rows[k]
                    left = float(b["strike"]) - float(a["strike"])
                    right_w = float(c["strike"]) - float(b["strike"])
                    if max(left, right_w) > cfg.max_width:
                        continue
                    base = "BUTTERFLY" if abs(left - right_w) <= 0.51 else "BROKEN_WING_BUTTERFLY"
                    specs.append(StructureSpec(f"{prefix}_{base}", (_leg(a, "buy_to_open"), _leg(b, "sell_to_open", 2), _leg(c, "buy_to_open")), max(left, right_w)))
                    specs.append(StructureSpec(f"REVERSE_{prefix}_{base}", (_leg(a, "sell_to_open"), _leg(b, "buy_to_open", 2), _leg(c, "sell_to_open")), max(left, right_w)))

    for rows, prefix in ((calls, "CALL"), (puts, "PUT")):
        for i in range(len(rows) - 3):
            for j in range(i + 1, min(i + 4, len(rows) - 2)):
                for k in range(j + 1, min(j + 4, len(rows) - 1)):
                    for m in range(k + 1, min(k + 4, len(rows))):
                        a, b, c, d = rows[i], rows[j], rows[k], rows[m]
                        width = float(d["strike"]) - float(a["strike"])
                        if width > 2.0 * cfg.max_width:
                            continue
                        wing1 = float(b["strike"]) - float(a["strike"])
                        wing2 = float(d["strike"]) - float(c["strike"])
                        base = "CONDOR" if abs(wing1 - wing2) <= 0.51 else "BROKEN_WING_CONDOR"
                        legs = (_leg(a, "buy_to_open"), _leg(b, "sell_to_open"), _leg(c, "sell_to_open"), _leg(d, "buy_to_open"))
                        reverse = tuple((o, "sell_to_open" if side.startswith("buy") else "buy_to_open", q) for o, side, q in legs)
                        specs.append(StructureSpec(f"{prefix}_{base}", legs, max(wing1, wing2)))
                        specs.append(StructureSpec(f"REVERSE_{prefix}_{base}", reverse, max(wing1, wing2)))
                        if abs(wing1 - wing2) > 0.51:
                            specs.append(StructureSpec(f"{prefix}_CHRISTMAS_TREE", legs, max(wing1, wing2)))
                            specs.append(StructureSpec(f"REVERSE_{prefix}_CHRISTMAS_TREE", reverse, max(wing1, wing2)))

    put_candidates = otm_puts[-8:]
    call_candidates = otm_calls[:8]
    for short_put in put_candidates:
        lower_puts = [p for p in puts if float(p["strike"]) < float(short_put["strike"]) and float(short_put["strike"]) - float(p["strike"]) <= cfg.max_width]
        for short_call in call_candidates:
            upper_calls = [c for c in calls if float(c["strike"]) > float(short_call["strike"]) and float(c["strike"]) - float(short_call["strike"]) <= cfg.max_width]
            for long_put in lower_puts[-3:]:
                for long_call in upper_calls[:3]:
                    left = float(short_put["strike"]) - float(long_put["strike"])
                    right_w = float(long_call["strike"]) - float(short_call["strike"])
                    base = "IRON_CONDOR" if abs(left - right_w) <= 0.51 else "BROKEN_WING_IRON_CONDOR"
                    legs = (_leg(long_put, "buy_to_open"), _leg(short_put, "sell_to_open"), _leg(short_call, "sell_to_open"), _leg(long_call, "buy_to_open"))
                    reverse = tuple((o, "sell_to_open" if side.startswith("buy") else "buy_to_open", q) for o, side, q in legs)
                    specs.append(StructureSpec(base, legs, max(left, right_w)))
                    specs.append(StructureSpec(f"REVERSE_{base}", reverse, max(left, right_w)))

    if atm_call and atm_put:
        center = float(atm_call["strike"])
        lower_puts = [p for p in puts if float(p["strike"]) < center and center - float(p["strike"]) <= cfg.max_width]
        upper_calls = [c for c in calls if float(c["strike"]) > center and float(c["strike"]) - center <= cfg.max_width]
        for lp in lower_puts[-4:]:
            for lc in upper_calls[:4]:
                width = max(center - float(lp["strike"]), float(lc["strike"]) - center)
                iron = (_leg(lp, "buy_to_open"), _leg(atm_put, "sell_to_open"), _leg(atm_call, "sell_to_open"), _leg(lc, "buy_to_open"))
                reverse = tuple((o, "sell_to_open" if side.startswith("buy") else "buy_to_open", q) for o, side, q in iron)
                specs.append(StructureSpec("IRON_BUTTERFLY", iron, width))
                specs.append(StructureSpec("REVERSE_IRON_BUTTERFLY", reverse, width))

    for put in otm_puts[-6:]:
        lower_wings = [p for p in puts if float(p["strike"]) < float(put["strike"]) and float(put["strike"]) - float(p["strike"]) <= cfg.max_width]
        for call in otm_calls[:6]:
            for wing in lower_wings[-2:]:
                specs.append(StructureSpec("BULLISH_RISK_REVERSAL_WITH_PUT_WING", (_leg(call, "buy_to_open"), _leg(put, "sell_to_open"), _leg(wing, "buy_to_open"))))
    for call in otm_calls[:6]:
        upper_wings = [c for c in calls if float(c["strike"]) > float(call["strike"]) and float(c["strike"]) - float(call["strike"]) <= cfg.max_width]
        for put in otm_puts[-6:]:
            for wing in upper_wings[:2]:
                specs.append(StructureSpec("BEARISH_RISK_REVERSAL_WITH_CALL_WING", (_leg(put, "buy_to_open"), _leg(call, "sell_to_open"), _leg(wing, "buy_to_open"))))

    common = sorted(set(float(c["strike"]) for c in calls) & set(float(p["strike"]) for p in puts))
    for low_k, high_k in combinations(common, 2):
        width = high_k - low_k
        if width <= 0 or width > cfg.max_width:
            continue
        cl, ch = _find(calls, "C", low_k), _find(calls, "C", high_k)
        pl, ph = _find(puts, "P", low_k), _find(puts, "P", high_k)
        if not all((cl, ch, pl, ph)):
            continue
        long_box = (_leg(cl, "buy_to_open"), _leg(ch, "sell_to_open"), _leg(ph, "buy_to_open"), _leg(pl, "sell_to_open"))
        short_box = tuple((o, "sell_to_open" if side.startswith("buy") else "buy_to_open", q) for o, side, q in long_box)
        specs.append(StructureSpec("LONG_BOX", long_box, width))
        specs.append(StructureSpec("SHORT_BOX", short_box, width))

    seen: set[tuple[Any, ...]] = set()
    unique: list[StructureSpec] = []
    for spec in specs:
        key = (spec.name, tuple((str(o["symbol"]), side, quantity) for o, side, quantity in spec.legs))
        if key not in seen:
            seen.add(key)
            unique.append(spec)
    return unique


def _planned_entry_price(spec: StructureSpec, cfg: V2OptimizerConfig) -> tuple[float, str]:
    quotes = _combo_quotes(spec)
    planned_cash = quotes["natural_cashflow"] + cfg.entry_mid_capture_fraction * (
        quotes["mid_cashflow"] - quotes["natural_cashflow"]
    )
    return abs(planned_cash), "credit" if planned_cash > 0 else "debit"


def generate_v2_candidates(
    config: SuiteConfig,
    prediction: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    optimizer_config: V2OptimizerConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = optimizer_config or V2OptimizerConfig()
    spot = float(prediction.get("spy_price") or 0.0)
    if spot <= 0 or not options:
        return []
    pool = liquid_contract_pool(options, spot, cfg)
    if not pool:
        return []
    specs = enumerate_bounded_risk_specs(pool, spot, cfg)
    screened = sorted(specs, key=lambda spec: _cheap_screen_score(spec, spot, cfg), reverse=True)
    screened = screened[: cfg.max_cheap_screen_structures]

    seed = int(str(prediction.get("feature_hash") or "0" * 8)[:8], 16)
    p_scenarios = _scenarios_from_frozen_distribution(prediction, key="p_price_quantiles", seed=seed, paths=6000)
    q_scenarios = _scenarios_from_frozen_distribution(prediction, key="q_price_quantiles", seed=seed ^ 0xA5A5A5A5, paths=6000)
    if p_scenarios is None:
        rng = np.random.default_rng(seed)
        mu = math.log(spot) + float(prediction.get("expected_return") or 0.0)
        sigma = max(float(prediction.get("sigma_return") or 0.0), 1e-6)
        z = rng.standard_t(df=7.0, size=6000) * math.sqrt(5.0 / 7.0)
        p_scenarios = np.exp(mu + sigma * z)
    if q_scenarios is None:
        q_scenarios = np.asarray(p_scenarios, dtype=float).copy()

    expiration = str(pool[0].get("expiration") or "")
    eval_config = config.model_copy(deep=True)
    eval_config.trading.fee_per_contract = 0.0
    eval_config.trading.slippage_fraction_of_spread = 0.0
    eval_config.trading.minimum_slippage = 0.0
    eval_config.strategy.require_positive_doubled_cost_ev = False
    eval_config.strategy.min_probability = 0.0
    eval_config.strategy.min_edge_to_uncertainty = 0.0
    eval_config.strategy.min_edge_dollars = 0.0

    valued: list[dict[str, Any]] = []
    for spec in screened[: cfg.max_full_valuations]:
        try:
            candidate = _evaluate_structure(
                name=spec.name,
                legs=_legacy_legs(spec),
                scenarios=p_scenarios,
                q_scenarios=q_scenarios,
                config=eval_config,
                prediction=prediction,
                expiration=expiration,
                width=spec.width,
            )
        except Exception:
            continue

        quotes = _combo_quotes(spec)
        spread = quotes["combined_spread"]
        recovered_spread_dollars = 0.5 * spread * 100.0 * (
            cfg.entry_mid_capture_fraction + cfg.exit_mid_capture_fraction
        )
        fee_dollars = 2.0 * spec.leg_contracts * cfg.estimated_pass_through_fee_per_contract_side
        v2_ev = float(candidate["expected_value"]) + recovered_spread_dollars - fee_dollars
        residual_drag = (
            spread * 100.0
            - recovered_spread_dollars
            + fee_dollars
            + cfg.leg_penalty_dollars * spec.leg_contracts
        )
        planned_entry_price, entry_kind = _planned_entry_price(spec, cfg)
        max_loss = float(candidate.get("max_loss") or math.inf)
        probability_profit = float(candidate.get("probability_profit") or 0.0)
        edge_to_drag = v2_ev / max(residual_drag, 0.50)
        drag_fraction = residual_drag / max(max_loss, 1.0)
        accepted = (
            max_loss <= min(cfg.max_loss_dollars, config.risk.maximum_trade_risk_dollars) + 1e-9
            and v2_ev >= cfg.min_expected_value_dollars
            and probability_profit >= cfg.min_probability_profit
            and drag_fraction <= cfg.max_execution_drag_fraction_of_risk
            and edge_to_drag >= cfg.min_edge_to_execution_drag
            and planned_entry_price > 0.0
        )

        candidate["strategy"] = spec.name
        candidate["status"] = "ELIGIBLE" if accepted else "REJECTED"
        candidate["expected_value"] = v2_ev
        candidate["entry_price"] = planned_entry_price
        candidate["entry_kind"] = entry_kind
        candidate["legs"] = [
            {
                "symbol": option["symbol"],
                "right": option["right"],
                "strike": float(option["strike"]),
                "side": side,
                "quantity": quantity,
            }
            for option, side, quantity in spec.legs
        ]
        candidate["score"] = (
            v2_ev / max(max_loss, 1.0)
            + 0.20 * (probability_profit - 0.5)
            + 0.03 * edge_to_drag
            - 0.12 * drag_fraction
            - 0.003 * spec.leg_contracts
        )
        reasons: list[str] = []
        if max_loss > min(cfg.max_loss_dollars, config.risk.maximum_trade_risk_dollars):
            reasons.append("risk_exceeds_cap")
        if v2_ev < cfg.min_expected_value_dollars:
            reasons.append("net_edge_below_threshold")
        if probability_profit < cfg.min_probability_profit:
            reasons.append("probability_profit_below_threshold")
        if drag_fraction > cfg.max_execution_drag_fraction_of_risk:
            reasons.append("execution_drag_too_large")
        if edge_to_drag < cfg.min_edge_to_execution_drag:
            reasons.append("edge_does_not_clear_execution_drag")
        candidate["rejection_reason"] = None if accepted else ",".join(reasons) or "v2_rejected"
        payload = dict(candidate.get("payload") or {})
        payload["v2"] = {
            "optimizer": asdict(cfg),
            "liquidity_pool_size": len(pool),
            "enumerated_structures": len(specs),
            "cheap_screened_structures": len(screened),
            "full_valuations": min(len(screened), cfg.max_full_valuations),
            "combined_spread": spread,
            "combo_mid_cashflow": quotes["mid_cashflow"],
            "combo_natural_cashflow": quotes["natural_cashflow"],
            "planned_entry_price": planned_entry_price,
            "recovered_spread_dollars": recovered_spread_dollars,
            "estimated_pass_through_fees_dollars": fee_dollars,
            "estimated_execution_drag_dollars": residual_drag,
            "execution_drag_fraction_of_risk": drag_fraction,
            "edge_to_execution_drag": edge_to_drag,
            "strategy_authority": "alpha_v2_optimizer",
            "beta_family_authority": False,
        }
        candidate["payload"] = payload
        valued.append(candidate)

    valued.sort(
        key=lambda c: (
            float(c.get("status") == "ELIGIBLE"),
            float(c.get("score") or -1e9),
            -len(c.get("legs") or []) if cfg.prefer_fewer_legs_tiebreak else 0,
        ),
        reverse=True,
    )
    return valued

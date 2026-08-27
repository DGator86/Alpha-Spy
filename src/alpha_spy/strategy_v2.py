from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import SuiteConfig
from .liquidity_v2 import liquid_option_pool, structure_execution_drag
from .strategy import _evaluate_structure, _scenarios_from_frozen_distribution

Leg = tuple[dict[str, Any], str]


@dataclass(frozen=True)
class _Spec:
    name: str
    legs: tuple[Leg, ...]
    width: float | None = None

    @property
    def distinct_legs(self) -> int:
        return len({(str(option.get("symbol")), side) for option, side in self.legs})

    @property
    def drag(self) -> float:
        return structure_execution_drag(list(self.legs))

    @property
    def key(self) -> tuple:
        return (
            self.name,
            tuple(
                sorted(
                    (str(option.get("symbol")), side)
                    for option, side in self.legs
                )
            ),
        )


def _rows(options: list[dict[str, Any]], right: str) -> list[dict[str, Any]]:
    return sorted(
        [option for option in options if str(option.get("right")) == right],
        key=lambda option: float(option.get("strike") or 0.0),
    )


def _width(a: dict[str, Any], b: dict[str, Any]) -> float:
    return abs(float(a["strike"]) - float(b["strike"]))


def _repeat(option: dict[str, Any], side: str, quantity: int) -> list[Leg]:
    return [(option, side)] * max(0, quantity)


def _add(
    specs: list[_Spec],
    name: str,
    legs: list[Leg],
    width: float | None = None,
) -> None:
    if not legs:
        return
    distinct = {(str(option.get("symbol")), side) for option, side in legs}
    if len(distinct) > 4:
        return
    specs.append(_Spec(name=name, legs=tuple(legs), width=width))


def _add_outrights(specs: list[_Spec], calls: list[dict[str, Any]], puts: list[dict[str, Any]]) -> None:
    for call in calls:
        _add(specs, "LONG_CALL", [(call, "buy_to_open")])
    for put in puts:
        _add(specs, "LONG_PUT", [(put, "buy_to_open")])


def _add_verticals(
    specs: list[_Spec],
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    max_width: float,
) -> None:
    for rows, right in ((calls, "C"), (puts, "P")):
        for index, lower in enumerate(rows):
            for upper in rows[index + 1 :]:
                width = _width(lower, upper)
                if not 0.5 <= width <= max_width:
                    continue
                if right == "C":
                    _add(
                        specs,
                        "CALL_DEBIT_SPREAD",
                        [(lower, "buy_to_open"), (upper, "sell_to_open")],
                        width,
                    )
                    _add(
                        specs,
                        "BEAR_CALL_CREDIT_SPREAD",
                        [(lower, "sell_to_open"), (upper, "buy_to_open")],
                        width,
                    )
                else:
                    _add(
                        specs,
                        "BULL_PUT_CREDIT_SPREAD",
                        [(lower, "buy_to_open"), (upper, "sell_to_open")],
                        width,
                    )
                    _add(
                        specs,
                        "PUT_DEBIT_SPREAD",
                        [(lower, "sell_to_open"), (upper, "buy_to_open")],
                        width,
                    )


def _add_two_sided_long_vol(
    specs: list[_Spec],
    near_calls: list[dict[str, Any]],
    near_puts: list[dict[str, Any]],
    spot: float,
    max_width: float,
) -> None:
    for call in near_calls:
        for put in near_puts:
            call_strike = float(call["strike"])
            put_strike = float(put["strike"])
            if abs(call_strike - put_strike) <= 0.51:
                _add(specs, "LONG_STRADDLE", [(call, "buy_to_open"), (put, "buy_to_open")])
                _add(
                    specs,
                    "LONG_STRAP",
                    [*_repeat(call, "buy_to_open", 2), (put, "buy_to_open")],
                )
                _add(
                    specs,
                    "LONG_STRIP",
                    [(call, "buy_to_open"), *_repeat(put, "buy_to_open", 2)],
                )
            if put_strike <= spot <= call_strike and call_strike - put_strike <= max_width * 2:
                _add(specs, "LONG_STRANGLE", [(call, "buy_to_open"), (put, "buy_to_open")])
            if call_strike <= spot <= put_strike and put_strike - call_strike <= max_width * 2:
                _add(specs, "LONG_GUTS", [(call, "buy_to_open"), (put, "buy_to_open")])


def _add_three_leg_families(
    specs: list[_Spec],
    rows: list[dict[str, Any]],
    right: str,
    max_width: float,
) -> None:
    count = len(rows)
    for first_index in range(count):
        for middle_index in range(first_index + 1, min(count, first_index + 6)):
            first, middle = rows[first_index], rows[middle_index]
            pair_width = _width(first, middle)
            if pair_width <= max_width:
                if right == "C":
                    short, long = first, middle
                    prefix = "CALL"
                else:
                    short, long = middle, first
                    prefix = "PUT"
                _add(
                    specs,
                    f"{prefix}_BACKSPREAD_1x2",
                    [(short, "sell_to_open"), *_repeat(long, "buy_to_open", 2)],
                    pair_width,
                )
                _add(
                    specs,
                    f"{prefix}_BACKSPREAD_1x3",
                    [(short, "sell_to_open"), *_repeat(long, "buy_to_open", 3)],
                    pair_width,
                )

            for last_index in range(middle_index + 1, min(count, middle_index + 6)):
                last = rows[last_index]
                left_width = _width(first, middle)
                right_width = _width(middle, last)
                if _width(first, last) > max_width * 2:
                    continue
                symmetric = abs(left_width - right_width) <= 0.51
                fly = "CALL_BUTTERFLY" if right == "C" else "PUT_BUTTERFLY"
                broken = (
                    "CALL_BROKEN_WING_BUTTERFLY"
                    if right == "C"
                    else "PUT_BROKEN_WING_BUTTERFLY"
                )
                reverse_fly = (
                    "REVERSE_CALL_BUTTERFLY"
                    if right == "C"
                    else "REVERSE_PUT_BUTTERFLY"
                )
                reverse_broken = (
                    "REVERSE_CALL_BROKEN_WING_BUTTERFLY"
                    if right == "C"
                    else "REVERSE_PUT_BROKEN_WING_BUTTERFLY"
                )
                width = max(left_width, right_width)
                _add(
                    specs,
                    fly if symmetric else broken,
                    [
                        (first, "buy_to_open"),
                        *_repeat(middle, "sell_to_open", 2),
                        (last, "buy_to_open"),
                    ],
                    width,
                )
                _add(
                    specs,
                    reverse_fly if symmetric else reverse_broken,
                    [
                        (first, "sell_to_open"),
                        *_repeat(middle, "buy_to_open", 2),
                        (last, "sell_to_open"),
                    ],
                    width,
                )
                tree = "CALL_CHRISTMAS_TREE" if right == "C" else "PUT_CHRISTMAS_TREE"
                reverse_tree = (
                    "REVERSE_CALL_CHRISTMAS_TREE"
                    if right == "C"
                    else "REVERSE_PUT_CHRISTMAS_TREE"
                )
                _add(
                    specs,
                    tree,
                    [
                        (first, "buy_to_open"),
                        *_repeat(middle, "sell_to_open", 3),
                        *_repeat(last, "buy_to_open", 2),
                    ],
                    width,
                )
                _add(
                    specs,
                    reverse_tree,
                    [
                        (first, "sell_to_open"),
                        *_repeat(middle, "buy_to_open", 3),
                        *_repeat(last, "sell_to_open", 2),
                    ],
                    width,
                )


def _add_same_right_condors(
    specs: list[_Spec],
    rows: list[dict[str, Any]],
    right: str,
    max_width: float,
) -> None:
    count = len(rows)
    for first_index in range(max(0, count - 3)):
        for second_index in range(first_index + 1, min(count, first_index + 4)):
            for third_index in range(second_index + 1, min(count, second_index + 4)):
                for fourth_index in range(third_index + 1, min(count, third_index + 4)):
                    first = rows[first_index]
                    second = rows[second_index]
                    third = rows[third_index]
                    fourth = rows[fourth_index]
                    if _width(first, fourth) > max_width * 2:
                        continue
                    first_wing = _width(first, second)
                    second_wing = _width(third, fourth)
                    symmetric = abs(first_wing - second_wing) <= 0.51
                    if right == "C":
                        normal = "CALL_CONDOR" if symmetric else "CALL_BROKEN_WING_CONDOR"
                        reverse = (
                            "REVERSE_CALL_CONDOR"
                            if symmetric
                            else "REVERSE_CALL_BROKEN_WING_CONDOR"
                        )
                    else:
                        normal = "PUT_CONDOR" if symmetric else "PUT_BROKEN_WING_CONDOR"
                        reverse = (
                            "REVERSE_PUT_CONDOR"
                            if symmetric
                            else "REVERSE_PUT_BROKEN_WING_CONDOR"
                        )
                    width = max(first_wing, second_wing)
                    _add(
                        specs,
                        normal,
                        [
                            (first, "buy_to_open"),
                            (second, "sell_to_open"),
                            (third, "sell_to_open"),
                            (fourth, "buy_to_open"),
                        ],
                        width,
                    )
                    _add(
                        specs,
                        reverse,
                        [
                            (first, "sell_to_open"),
                            (second, "buy_to_open"),
                            (third, "buy_to_open"),
                            (fourth, "sell_to_open"),
                        ],
                        width,
                    )


def _add_iron_families(
    specs: list[_Spec],
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    max_width: float,
) -> None:
    for put_short in puts:
        put_strike = float(put_short["strike"])
        put_wings = [
            put
            for put in puts
            if 0.5 <= put_strike - float(put["strike"]) <= max_width
        ]
        for call_short in calls:
            call_strike = float(call_short["strike"])
            if put_strike > call_strike or call_strike - put_strike > max_width * 2:
                continue
            call_wings = [
                call
                for call in calls
                if 0.5 <= float(call["strike"]) - call_strike <= max_width
            ]
            for put_wing in put_wings[:4]:
                for call_wing in call_wings[:4]:
                    put_width = put_strike - float(put_wing["strike"])
                    call_width = float(call_wing["strike"]) - call_strike
                    butterfly = abs(put_strike - call_strike) <= 0.51
                    broken = abs(put_width - call_width) > 0.51
                    if butterfly:
                        name = "BROKEN_WING_IRON_BUTTERFLY" if broken else "IRON_BUTTERFLY"
                    else:
                        name = "BROKEN_WING_IRON_CONDOR" if broken else "IRON_CONDOR"
                    width = max(put_width, call_width)
                    _add(
                        specs,
                        name,
                        [
                            (put_wing, "buy_to_open"),
                            (put_short, "sell_to_open"),
                            (call_short, "sell_to_open"),
                            (call_wing, "buy_to_open"),
                        ],
                        width,
                    )
                    _add(
                        specs,
                        f"REVERSE_{name}",
                        [
                            (put_wing, "sell_to_open"),
                            (put_short, "buy_to_open"),
                            (call_short, "buy_to_open"),
                            (call_wing, "sell_to_open"),
                        ],
                        width,
                    )


def _add_winged_risk_reversals(
    specs: list[_Spec],
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    near_calls: list[dict[str, Any]],
    near_puts: list[dict[str, Any]],
    spot: float,
    max_width: float,
) -> None:
    for put_short in puts:
        for put_wing in puts:
            width = float(put_short["strike"]) - float(put_wing["strike"])
            if not 0.5 <= width <= max_width:
                continue
            for call_long in near_calls[:6]:
                if float(call_long["strike"]) >= spot - 1.0:
                    _add(
                        specs,
                        "BULLISH_RISK_REVERSAL_WITH_PUT_WING",
                        [
                            (put_wing, "buy_to_open"),
                            (put_short, "sell_to_open"),
                            (call_long, "buy_to_open"),
                        ],
                        width,
                    )
    for call_short in calls:
        for call_wing in calls:
            width = float(call_wing["strike"]) - float(call_short["strike"])
            if not 0.5 <= width <= max_width:
                continue
            for put_long in near_puts[:6]:
                if float(put_long["strike"]) <= spot + 1.0:
                    _add(
                        specs,
                        "BEARISH_RISK_REVERSAL_WITH_CALL_WING",
                        [
                            (put_long, "buy_to_open"),
                            (call_short, "sell_to_open"),
                            (call_wing, "buy_to_open"),
                        ],
                        width,
                    )


def _add_boxes(
    specs: list[_Spec],
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    max_width: float,
) -> None:
    call_by_strike = {float(option["strike"]): option for option in calls}
    put_by_strike = {float(option["strike"]): option for option in puts}
    common = sorted(set(call_by_strike) & set(put_by_strike))
    for index, low in enumerate(common):
        for high in common[index + 1 :]:
            if high - low > max_width:
                continue
            low_call, high_call = call_by_strike[low], call_by_strike[high]
            low_put, high_put = put_by_strike[low], put_by_strike[high]
            _add(
                specs,
                "LONG_BOX",
                [
                    (low_call, "buy_to_open"),
                    (high_call, "sell_to_open"),
                    (low_put, "sell_to_open"),
                    (high_put, "buy_to_open"),
                ],
                high - low,
            )
            _add(
                specs,
                "SHORT_BOX",
                [
                    (low_call, "sell_to_open"),
                    (high_call, "buy_to_open"),
                    (low_put, "buy_to_open"),
                    (high_put, "sell_to_open"),
                ],
                high - low,
            )


def _geometry_specs(
    options: list[dict[str, Any]],
    spot: float,
    max_width: float = 10.0,
) -> list[_Spec]:
    calls = _rows(options, "C")
    puts = _rows(options, "P")
    near_calls = sorted(calls, key=lambda option: abs(float(option["strike"]) - spot))[:8]
    near_puts = sorted(puts, key=lambda option: abs(float(option["strike"]) - spot))[:8]
    specs: list[_Spec] = []

    _add_outrights(specs, calls, puts)
    _add_verticals(specs, calls, puts, max_width)
    _add_two_sided_long_vol(specs, near_calls, near_puts, spot, max_width)
    _add_three_leg_families(specs, calls, "C", max_width)
    _add_three_leg_families(specs, puts, "P", max_width)
    _add_same_right_condors(specs, calls, "C", max_width)
    _add_same_right_condors(specs, puts, "P", max_width)
    _add_iron_families(specs, calls, puts, max_width)
    _add_winged_risk_reversals(
        specs,
        calls,
        puts,
        near_calls,
        near_puts,
        spot,
        max_width,
    )
    _add_boxes(specs, calls, puts, max_width)

    deduped: dict[tuple, _Spec] = {}
    for spec in specs:
        deduped.setdefault(spec.key, spec)
    return list(deduped.values())


def _category(name: str) -> str:
    directional = {
        "LONG_CALL",
        "LONG_PUT",
        "CALL_DEBIT_SPREAD",
        "PUT_DEBIT_SPREAD",
        "BULL_PUT_CREDIT_SPREAD",
        "BEAR_CALL_CREDIT_SPREAD",
        "BULLISH_RISK_REVERSAL_WITH_PUT_WING",
        "BEARISH_RISK_REVERSAL_WITH_CALL_WING",
    }
    range_or_conditional = {
        "IRON_CONDOR",
        "IRON_BUTTERFLY",
        "BROKEN_WING_IRON_CONDOR",
        "BROKEN_WING_IRON_BUTTERFLY",
        "CALL_CONDOR",
        "PUT_CONDOR",
        "CALL_BUTTERFLY",
        "PUT_BUTTERFLY",
        "CALL_BROKEN_WING_BUTTERFLY",
        "PUT_BROKEN_WING_BUTTERFLY",
        "CALL_CHRISTMAS_TREE",
        "PUT_CHRISTMAS_TREE",
    }
    if name in directional:
        return "directional"
    if name.startswith("LONG_") or "BACKSPREAD" in name or name.startswith("REVERSE_"):
        return "convex"
    if name in range_or_conditional:
        return "range_or_conditional"
    return "conditional"


def _v2_requalify(
    candidate: dict[str, Any],
    prediction: dict[str, Any],
    config: SuiteConfig,
) -> dict[str, Any]:
    payload = candidate.setdefault("payload", {})
    beta = (prediction.get("payload") or {}).get("beta_v2") or {}
    family = _category(str(candidate.get("strategy") or ""))
    expected_value = float(candidate.get("expected_value") or 0.0)
    pop = float(candidate.get("probability_profit") or 0.0)
    max_loss = float(candidate.get("max_loss") or 0.0)
    pnl_std = max(float(payload.get("p_pnl_std") or 0.0), 1.0)
    q_edge = float(payload.get("q_executable_edge") or 0.0)
    uncertainty_ratio = float(payload.get("uncertainty_ratio") or 0.0)
    edge_to_uncertainty = float(payload.get("edge_to_uncertainty") or 0.0)
    doubled = float(payload.get("doubled_cost_expected_value") or 0.0)
    drag = float(payload.get("combined_spread") or 0.0) * 100.0
    leg_count = sum(int(leg.get("quantity", 1)) for leg in candidate.get("legs", []))

    hard_ok = (
        expected_value >= config.strategy.min_edge_dollars * 100.0
        and pop >= min(config.strategy.min_probability, 0.58)
        and edge_to_uncertainty >= config.strategy.min_edge_to_uncertainty
        and 0.0 < max_loss <= config.risk.maximum_trade_risk_dollars
        and doubled > 0.0
    )

    p_big = float(beta.get("probability_big_move", 0.5))
    mag_trust = float(beta.get("magnitude_trust", 0.0))
    dir_trust = float(beta.get("direction_trust", 0.0))
    beta_edge = float(beta.get("validated_direction_edge", 0.0))
    beta_adjustment = 0.0
    name = str(candidate.get("strategy") or "")
    if family == "directional" and dir_trust > 0:
        bullish = name in {
            "LONG_CALL",
            "CALL_DEBIT_SPREAD",
            "BULL_PUT_CREDIT_SPREAD",
            "BULLISH_RISK_REVERSAL_WITH_PUT_WING",
        }
        direction = 1.0 if bullish else -1.0
        beta_adjustment += 0.12 * dir_trust * direction * beta_edge
    elif family == "convex" and mag_trust > 0:
        beta_adjustment += 0.08 * mag_trust * (p_big - 0.5) * 2.0
    elif family == "range_or_conditional" and mag_trust > 0:
        beta_adjustment += 0.06 * mag_trust * (0.5 - p_big) * 2.0

    liquidity_efficiency = expected_value / max(drag + 0.50 * leg_count, 1.0)
    # Recompute from V2 economics. Legacy family-fit penalties are deliberately
    # not inherited; Beta state is a prior/score adjustment rather than a veto.
    v2_score = (
        expected_value / max(max_loss, 1.0)
        + 0.25 * (pop - 0.5)
        + 0.05 * expected_value / pnl_std
        - 0.10 * uncertainty_ratio
        + 0.05 * q_edge / max(max_loss, 1.0)
        + 0.08 * liquidity_efficiency
        + beta_adjustment
    )
    payload.update(
        {
            "v2": True,
            "v2_category": family,
            "execution_drag_dollars": drag,
            "contract_legs": leg_count,
            "liquidity_efficiency": liquidity_efficiency,
            "beta_v2_adjustment": beta_adjustment,
            "legacy_strategy_fit_ignored": True,
        }
    )
    candidate["score"] = v2_score
    candidate["status"] = "ELIGIBLE" if hard_ok else "REJECTED"
    candidate["rejection_reason"] = None if hard_ok else "v2_economic_gate"
    return candidate


def generate_candidates_v2(
    config: SuiteConfig,
    prediction: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    max_full_evaluations: int = 1800,
    max_returned: int = 300,
) -> list[dict[str, Any]]:
    """Liquidity-first exhaustive bounded-risk tournament."""
    spot = float(prediction.get("spy_price") or 0.0)
    if spot <= 0 or not options:
        return []
    beta = (prediction.get("payload") or {}).get("beta_v2") or {}
    expected_abs = float(beta.get("expected_abs_move_bps") or 0.0)
    move_dollars = spot * expected_abs / 10_000.0
    distance = max(8.0, min(18.0, 4.0 * move_dollars + 5.0))
    pool = liquid_option_pool(
        options,
        spot=spot,
        max_distance_dollars=distance,
        max_spread_dollars=0.05,
        max_relative_spread=0.25,
        min_open_interest=10,
        min_volume=0,
        per_right_limit=28,
    )
    if not pool:
        return []

    specs = _geometry_specs(pool, spot, max_width=10.0)
    specs.sort(
        key=lambda spec: (
            spec.drag + 0.35 * spec.distinct_legs,
            spec.distinct_legs,
        )
    )
    specs = specs[:max_full_evaluations]

    seed = int(str(prediction.get("feature_hash") or "0")[:8], 16)
    scenarios = _scenarios_from_frozen_distribution(
        prediction,
        key="p_price_quantiles",
        seed=seed,
        paths=6000,
    )
    q_scenarios = _scenarios_from_frozen_distribution(
        prediction,
        key="q_price_quantiles",
        seed=seed ^ 0xA5A5A5A5,
        paths=6000,
    )
    if scenarios is None or q_scenarios is None:
        return []
    expiration = str(pool[0].get("expiration") or "")

    valuation_config = config.model_copy(deep=True)
    valuation_config.trading.fee_per_contract = 0.0
    valuation_config.trading.minimum_slippage = 0.0
    valuation_config.trading.slippage_fraction_of_spread = min(
        valuation_config.trading.slippage_fraction_of_spread,
        0.10,
    )
    valuation_config.strategy.require_multi_horizon_alignment = False

    candidates: list[dict[str, Any]] = []
    for spec in specs:
        try:
            candidate = _evaluate_structure(
                name=spec.name,
                legs=list(spec.legs),
                scenarios=scenarios,
                q_scenarios=q_scenarios,
                config=valuation_config,
                prediction=prediction,
                expiration=expiration,
                width=spec.width,
            )
        except Exception:
            continue
        candidates.append(_v2_requalify(candidate, prediction, config))

    candidates.sort(
        key=lambda candidate: (
            candidate.get("status") == "ELIGIBLE",
            float(candidate.get("score") or -1e9),
            float(candidate.get("expected_value") or -1e9),
        ),
        reverse=True,
    )
    return candidates[:max_returned]

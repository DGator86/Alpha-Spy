from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import SuiteConfig
from .liquidity_v2 import liquid_option_pool, structure_execution_drag
from .strategy import _evaluate_structure, _scenarios_from_frozen_distribution


@dataclass(frozen=True)
class _Spec:
    name: str
    legs: tuple[tuple[dict[str, Any], str], ...]
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
                    (
                        str(option.get("symbol")),
                        side,
                    )
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


def _repeat(option: dict[str, Any], side: str, quantity: int) -> list[tuple[dict[str, Any], str]]:
    return [(option, side)] * max(0, quantity)


def _add(specs: list[_Spec], name: str, legs: list[tuple[dict[str, Any], str]], width: float | None = None) -> None:
    if not legs:
        return
    # Broker supports <=4 distinct option legs; quantities can exceed one.
    distinct = {(str(option.get("symbol")), side) for option, side in legs}
    if len(distinct) > 4:
        return
    specs.append(_Spec(name=name, legs=tuple(legs), width=width))


def _geometry_specs(options: list[dict[str, Any]], spot: float, max_width: float = 10.0) -> list[_Spec]:
    calls = _rows(options, "C")
    puts = _rows(options, "P")
    specs: list[_Spec] = []

    # Outrights: let P/Q choose among several highly liquid strikes rather than
    # anchoring one contract to the point forecast.
    for call in calls:
        _add(specs, "LONG_CALL", [(call, "buy_to_open")])
    for put in puts:
        _add(specs, "LONG_PUT", [(put, "buy_to_open")])

    # Verticals in both debit and credit orientations.
    for rows, right in ((calls, "C"), (puts, "P")):
        for i, lower in enumerate(rows):
            for upper in rows[i + 1 :]:
                width = _width(lower, upper)
                if width < 0.5 or width > max_width:
                    continue
                if right == "C":
                    _add(specs, "CALL_DEBIT_SPREAD", [(lower, "buy_to_open"), (upper, "sell_to_open")], width)
                    _add(specs, "BEAR_CALL_CREDIT_SPREAD", [(lower, "sell_to_open"), (upper, "buy_to_open")], width)
                else:
                    _add(specs, "BULL_PUT_CREDIT_SPREAD", [(lower, "buy_to_open"), (upper, "sell_to_open")], width)
                    _add(specs, "PUT_DEBIT_SPREAD", [(lower, "sell_to_open"), (upper, "buy_to_open")], width)

    # Two-sided long-vol shapes around spot.
    near_calls = sorted(calls, key=lambda o: abs(float(o["strike"]) - spot))[:8]
    near_puts = sorted(puts, key=lambda o: abs(float(o["strike"]) - spot))[:8]
    for call in near_calls:
        for put in near_puts:
            ck = float(call["strike"])
            pk = float(put["strike"])
            if abs(ck - pk) <= 0.51:
                _add(specs, "LONG_STRADDLE", [(call, "buy_to_open"), (put, "buy_to_open")])
                _add(specs, "LONG_STRAP", _repeat(call, "buy_to_open", 2) + [(put, "buy_to_open")])
                _add(specs, "LONG_STRIP", [(call, "buy_to_open")] + _repeat(put, "buy_to_open", 2))
            if pk <= spot <= ck and ck - pk <= max_width * 2:
                _add(specs, "LONG_STRANGLE", [(call, "buy_to_open"), (put, "buy_to_open")])
            if ck <= spot <= pk and pk - ck <= max_width * 2:
                _add(specs, "LONG_GUTS", [(call, "buy_to_open"), (put, "buy_to_open")])

    # Same-right 3-leg families: symmetric/broken butterflies, reverse flies,
    # Christmas trees and backspreads.
    for rows, right in ((calls, "C"), (puts, "P")):
        n = len(rows)
        for i in range(n):
            for j in range(i + 1, min(n, i + 6)):
                for k in range(j + 1, min(n, j + 6)):
                    a, b, c = rows[i], rows[j], rows[k]
                    left = _width(a, b)
                    right_w = _width(b, c)
                    total = _width(a, c)
                    if total > max_width * 2:
                        continue
                    fly = "CALL_BUTTERFLY" if right == "C" else "PUT_BUTTERFLY"
                    bw = "CALL_BROKEN_WING_BUTTERFLY" if right == "C" else "PUT_BROKEN_WING_BUTTERFLY"
                    rev = "REVERSE_CALL_BUTTERFLY" if right == "C" else "REVERSE_PUT_BUTTERFLY"
                    rev_bw = "REVERSE_CALL_BROKEN_WING_BUTTERFLY" if right == "C" else "REVERSE_PUT_BROKEN_WING_BUTTERFLY"
                    if abs(left - right_w) <= 0.51:
                        _add(specs, fly, [(a, "buy_to_open")] + _repeat(b, "sell_to_open", 2) + [(c, "buy_to_open")], max(left, right_w))
                        _add(specs, rev, [(a, "sell_to_open")] + _repeat(b, "buy_to_open", 2) + [(c, "sell_to_open")], max(left, right_w))
                    else:
                        _add(specs, bw, [(a, "buy_to_open")] + _repeat(b, "sell_to_open", 2) + [(c, "buy_to_open")], max(left, right_w))
                        _add(specs, rev_bw, [(a, "sell_to_open")] + _repeat(b, "buy_to_open", 2) + [(c, "sell_to_open")], max(left, right_w))

                    # Christmas tree: +1 / -3 / +2 has zero terminal slope.
                    tree = "CALL_CHRISTMAS_TREE" if right == "C" else "PUT_CHRISTMAS_TREE"
                    reverse_tree = "REVERSE_CALL_CHRISTMAS_TREE" if right == "C" else "REVERSE_PUT_CHRISTMAS_TREE"
                    _add(specs, tree, [(a, "buy_to_open")] + _repeat(b, "sell_to_open", 3) + _repeat(c, "buy_to_open", 2), max(left, right_w))
                    _add(specs, reverse_tree, [(a, "sell_to_open")] + _repeat(b, "buy_to_open", 3) + _repeat(c, "sell_to_open", 2), max(left, right_w))

                # Backspreads need only a pair.  Calls sell lower/buy higher;
                # puts sell higher/buy lower.  Tail risk is defined by the long ratio.
                a, b = rows[i], rows[j]
                width = _width(a, b)
                if width <= max_width:
                    if right == "C":
                        short, long = a, b
                        prefix = "CALL"
                    else:
                        short, long = b, a
                        prefix = "PUT"
                    _add(specs, f"{prefix}_BACKSPREAD_1x2", [(short, "sell_to_open")] + _repeat(long, "buy_to_open", 2), width)
                    _add(specs, f"{prefix}_BACKSPREAD_1x3", [(short, "sell_to_open")] + _repeat(long, "buy_to_open", 3), width)

        # Same-right condors and broken-wing/reverse variants using four local strikes.
        for i in range(max(0, n - 3)):
            for j in range(i + 1, min(n, i + 4)):
                for k in range(j + 1, min(n, j + 4)):
                    for l in range(k + 1, min(n, k + 4)):
                        a, b, c, d = rows[i], rows[j], rows[k], rows[l]
                        if _width(a, d) > max_width * 2:
                            continue
                        w1, w2 = _width(a, b), _width(c, d)
                        condor = "CALL_CONDOR" if right == "C" else "PUT_CONDOR"
                        broken = "CALL_BROKEN_WING_CONDOR" if right == "C" else "PUT_BROKEN_WING_CONDOR"
                        reverse = "REVERSE_CALL_CONDOR" if right == "C" else "REVERSE_PUT_CONDOR"
                        reverse_broken = "REVERSE_CALL_BROKEN_WING_CONDOR" if right == "C" else "REVERSE_PUT_BROKEN_WING_CONDOR"
                        normal_name = condor if abs(w1 - w2) <= 0.51 else broken
                        reverse_name = reverse if abs(w1 - w2) <= 0.51 else reverse_broken
                        _add(specs, normal_name, [(a, "buy_to_open"), (b, "sell_to_open"), (c, "sell_to_open"), (d, "buy_to_open")], max(w1, w2))
                        _add(specs, reverse_name, [(a, "sell_to_open"), (b, "buy_to_open"), (c, "buy_to_open"), (d, "sell_to_open")], max(w1, w2))

    # Iron structures and winged risk reversals.
    for put_short in puts:
        psk = float(put_short["strike"])
        put_wings = [p for p in puts if 0.5 <= psk - float(p["strike"]) <= max_width]
        for call_short in calls:
            csk = float(call_short["strike"])
            if psk > csk or csk - psk > max_width * 2:
                continue
            call_wings = [c for c in calls if 0.5 <= float(c["strike"]) - csk <= max_width]
            for pwing in put_wings[:4]:
                for cwing in call_wings[:4]:
                    pw = psk - float(pwing["strike"])
                    cw = float(cwing["strike"]) - csk
                    iron_name = "IRON_BUTTERFLY" if abs(psk - csk) <= 0.51 else "IRON_CONDOR"
                    if abs(pw - cw) > 0.51:
                        iron_name = "BROKEN_WING_IRON_BUTTERFLY" if abs(psk - csk) <= 0.51 else "BROKEN_WING_IRON_CONDOR"
                    _add(specs, iron_name, [(pwing, "buy_to_open"), (put_short, "sell_to_open"), (call_short, "sell_to_open"), (cwing, "buy_to_open")], max(pw, cw))
                    reverse_name = "REVERSE_" + iron_name
                    _add(specs, reverse_name, [(pwing, "sell_to_open"), (put_short, "buy_to_open"), (call_short, "buy_to_open"), (cwing, "sell_to_open")], max(pw, cw))

    # Risk reversals with a defined-risk wing.
    for put_short in puts:
        for put_wing in puts:
            if not 0.5 <= float(put_short["strike"]) - float(put_wing["strike"]) <= max_width:
                continue
            for call_long in near_calls[:6]:
                if float(call_long["strike"]) < spot - 1.0:
                    continue
                _add(specs, "BULLISH_RISK_REVERSAL_WITH_PUT_WING", [(put_wing, "buy_to_open"), (put_short, "sell_to_open"), (call_long, "buy_to_open")], _width(put_short, put_wing))
    for call_short in calls:
        for call_wing in calls:
            if not 0.5 <= float(call_wing["strike"]) - float(call_short["strike"]) <= max_width:
                continue
            for put_long in near_puts[:6]:
                if float(put_long["strike"]) > spot + 1.0:
                    continue
                _add(specs, "BEARISH_RISK_REVERSAL_WITH_CALL_WING", [(put_long, "buy_to_open"), (call_short, "sell_to_open"), (call_wing, "buy_to_open")], _width(call_short, call_wing))

    # Boxes: useful as a sanity/control family and should normally lose to NO_TRADE
    # after executable quotes unless the market is crossed/mispriced.
    call_by_strike = {float(o["strike"]): o for o in calls}
    put_by_strike = {float(o["strike"]): o for o in puts}
    common = sorted(set(call_by_strike) & set(put_by_strike))
    for i, low in enumerate(common):
        for high in common[i + 1 :]:
            if high - low > max_width:
                continue
            lc, hc = call_by_strike[low], call_by_strike[high]
            lp, hp = put_by_strike[low], put_by_strike[high]
            _add(specs, "LONG_BOX", [(lc, "buy_to_open"), (hc, "sell_to_open"), (lp, "sell_to_open"), (hp, "buy_to_open")], high - low)
            _add(specs, "SHORT_BOX", [(lc, "sell_to_open"), (hc, "buy_to_open"), (lp, "buy_to_open"), (hp, "sell_to_open")], high - low)

    deduped: dict[tuple, _Spec] = {}
    for spec in specs:
        deduped.setdefault(spec.key, spec)
    return list(deduped.values())


def _category(name: str) -> str:
    if name in {"LONG_CALL", "LONG_PUT", "CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD", "BULL_PUT_CREDIT_SPREAD", "BEAR_CALL_CREDIT_SPREAD", "BULLISH_RISK_REVERSAL_WITH_PUT_WING", "BEARISH_RISK_REVERSAL_WITH_CALL_WING"}:
        return "directional"
    if name.startswith("LONG_") or "BACKSPREAD" in name or name.startswith("REVERSE_"):
        return "convex"
    if name in {"IRON_CONDOR", "IRON_BUTTERFLY", "BROKEN_WING_IRON_CONDOR", "BROKEN_WING_IRON_BUTTERFLY", "CALL_CONDOR", "PUT_CONDOR", "CALL_BUTTERFLY", "PUT_BUTTERFLY", "CALL_BROKEN_WING_BUTTERFLY", "PUT_BROKEN_WING_BUTTERFLY", "CALL_CHRISTMAS_TREE", "PUT_CHRISTMAS_TREE"}:
        return "range_or_conditional"
    return "conditional"


def _v2_requalify(candidate: dict[str, Any], prediction: dict[str, Any], config: SuiteConfig) -> dict[str, Any]:
    payload = candidate.setdefault("payload", {})
    beta = (prediction.get("payload") or {}).get("beta_v2") or {}
    family = _category(str(candidate.get("strategy") or ""))
    expected_value = float(candidate.get("expected_value") or 0.0)
    pop = float(candidate.get("probability_profit") or 0.0)
    max_loss = float(candidate.get("max_loss") or 0.0)
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
        bullish = name in {"LONG_CALL", "CALL_DEBIT_SPREAD", "BULL_PUT_CREDIT_SPREAD", "BULLISH_RISK_REVERSAL_WITH_PUT_WING"}
        direction = 1.0 if bullish else -1.0
        beta_adjustment += 0.12 * dir_trust * direction * beta_edge
    elif family == "convex" and mag_trust > 0:
        beta_adjustment += 0.08 * mag_trust * (p_big - 0.5) * 2.0
    elif family == "range_or_conditional" and mag_trust > 0:
        beta_adjustment += 0.06 * mag_trust * (0.5 - p_big) * 2.0

    liquidity_efficiency = expected_value / max(drag + 0.50 * leg_count, 1.0)
    v2_score = float(candidate.get("score") or 0.0) + 0.08 * liquidity_efficiency + beta_adjustment
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
    """Liquidity-first exhaustive bounded-risk tournament.

    Geometry generation is broad; the expensive P/Q repricer only sees the
    lowest-friction subset.  This removes the legacy 40-candidate geometry choke
    point while keeping a one-minute live cycle computationally bounded.
    """
    spot = float(prediction.get("spy_price") or 0.0)
    if spot <= 0 or not options:
        return []
    expected_abs = float(((prediction.get("payload") or {}).get("beta_v2") or {}).get("expected_abs_move_bps") or 0.0)
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
    # Complexity does not get an arbitrary veto; it simply has to overcome the
    # extra quoted markets it consumes.
    specs.sort(key=lambda spec: (spec.drag + 0.35 * spec.distinct_legs, spec.distinct_legs))
    specs = specs[:max_full_evaluations]

    seed = int(str(prediction.get("feature_hash") or "0")[:8], 16)
    scenarios = _scenarios_from_frozen_distribution(
        prediction, key="p_price_quantiles", seed=seed, paths=6000
    )
    q_scenarios = _scenarios_from_frozen_distribution(
        prediction, key="q_price_quantiles", seed=seed ^ 0xA5A5A5A5, paths=6000
    )
    if scenarios is None or q_scenarios is None:
        return []
    expiration = str(pool[0].get("expiration") or "")

    # Tradier Pro commission on equity/ETF options is subscription-based, not a
    # per-contract $0.65 charge.  Broad ranking therefore uses zero commission;
    # the finalist must still pass broker preview fees in the V2 service.
    valuation_config = config.model_copy(deep=True)
    valuation_config.trading.fee_per_contract = 0.0
    valuation_config.trading.minimum_slippage = 0.0
    valuation_config.trading.slippage_fraction_of_spread = min(
        valuation_config.trading.slippage_fraction_of_spread, 0.10
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

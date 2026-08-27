from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

import numpy as np

from .strategy_v2_complete import generate_v2_candidates as generate_complete_candidates

LONG_VOL_FAMILIES = {
    "LONG_STRADDLE",
    "LONG_STRANGLE",
    "LONG_GUTS",
    "LONG_STRAP",
    "LONG_STRIP",
    "CALL_BACKSPREAD_1x2",
    "CALL_BACKSPREAD_1x3",
    "PUT_BACKSPREAD_1x2",
    "PUT_BACKSPREAD_1x3",
    "REVERSE_CALL_BUTTERFLY",
    "REVERSE_PUT_BUTTERFLY",
    "REVERSE_CALL_BROKEN_WING_BUTTERFLY",
    "REVERSE_PUT_BROKEN_WING_BUTTERFLY",
    "REVERSE_IRON_BUTTERFLY",
    "REVERSE_BROKEN_WING_IRON_BUTTERFLY",
    "REVERSE_CALL_CONDOR",
    "REVERSE_PUT_CONDOR",
    "REVERSE_CALL_BROKEN_WING_CONDOR",
    "REVERSE_PUT_BROKEN_WING_CONDOR",
    "REVERSE_IRON_CONDOR",
    "REVERSE_BROKEN_WING_IRON_CONDOR",
}

RANGE_FAMILIES = {
    "CALL_BUTTERFLY",
    "PUT_BUTTERFLY",
    "CALL_BROKEN_WING_BUTTERFLY",
    "PUT_BROKEN_WING_BUTTERFLY",
    "IRON_BUTTERFLY",
    "BROKEN_WING_IRON_BUTTERFLY",
    "CALL_CONDOR",
    "PUT_CONDOR",
    "CALL_BROKEN_WING_CONDOR",
    "PUT_BROKEN_WING_CONDOR",
    "IRON_CONDOR",
    "BROKEN_WING_IRON_CONDOR",
    "CALL_CHRISTMAS_TREE",
    "PUT_CHRISTMAS_TREE",
    "LONG_BOX",
    "SHORT_BOX",
}


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    cumulative = np.cumsum(weights)
    return float(values[min(np.searchsorted(cumulative, q * cumulative[-1]), len(values) - 1)])


def _state(beta_opportunity: dict[str, Any]) -> dict[str, Any] | None:
    state = beta_opportunity.get("predictive_state")
    if not isinstance(state, dict) or not bool(state.get("ready")):
        return None
    outcomes = state.get("analog_y15_bps") or []
    weights = state.get("analog_weights") or []
    if len(outcomes) < 25 or len(outcomes) != len(weights):
        return None
    return state


def attach_empirical_state_p(
    prediction: dict[str, Any],
    beta_opportunity: dict[str, Any],
) -> dict[str, Any]:
    """Replace Alpha P with Beta's empirical state-conditioned future distribution.

    Q is deliberately untouched. HGB supplies the mean only when its independently
    validated direction signal is eligible; tree-proximity analogs supply the shape,
    tails, path uncertainty, and regime evidence. This mirrors the causal research
    replay instead of rebuilding P as a Gaussian.
    """
    state = _state(beta_opportunity)
    if state is None:
        return deepcopy(prediction)
    out = deepcopy(prediction)
    spot = float(out.get("spy_price") or 0.0)
    if spot <= 0:
        return out

    outcomes = np.asarray(state["analog_y15_bps"], dtype=float)
    weights = np.asarray(state["analog_weights"], dtype=float)
    finite = np.isfinite(outcomes) & np.isfinite(weights) & (weights >= 0)
    outcomes, weights = outcomes[finite], weights[finite]
    if outcomes.size < 25 or float(weights.sum()) <= 0:
        return out
    weights /= weights.sum()
    analog_mean = float(np.dot(weights, outcomes))
    conformal = float(np.clip(float(state.get("conformal_scale") or 1.0), 0.85, 1.50))

    hgb = beta_opportunity.get("hgb_direction") or {}
    hgb_eligible = isinstance(hgb, dict) and bool(hgb.get("eligible"))
    if hgb_eligible:
        target_mean_bps = float(hgb.get("expected_return_bps") or 0.0)
        mean_source = "hgb_direction"
    else:
        target_mean_bps = 0.15 * float(state.get("direct_pred_15") or 0.0)
        mean_source = "state_shrunk"
    scenario_bps = target_mean_bps + (outcomes - analog_mean) * conformal
    scenario_returns = scenario_bps / 10_000.0
    scenario_prices = spot * np.maximum(1e-6, 1.0 + scenario_returns)

    payload = dict(out.get("payload") or {})
    distribution = dict(payload.get("distribution") or {})
    grid = np.asarray(distribution.get("probability_grid") or [], dtype=float)
    if grid.size < 9 or np.any(~np.isfinite(grid)) or np.any(np.diff(grid) <= 0):
        grid = np.asarray(
            [0.01, 0.025, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.975, 0.99],
            dtype=float,
        )
    p_quantiles = [_weighted_quantile(scenario_prices, weights, float(q)) for q in grid]
    p_mean = float(np.dot(weights, scenario_returns))
    p_sigma = math.sqrt(max(float(np.dot(weights, (scenario_returns - p_mean) ** 2)), 1e-12))
    p_up = float(np.dot(weights, (scenario_returns > 0).astype(float)))

    # Never rewrite q_* fields. They continue to come from the live option surface.
    distribution["probability_grid"] = grid.tolist()
    distribution["p_price_quantiles"] = p_quantiles
    distribution["p_expected_return"] = p_mean
    distribution["p_volatility"] = p_sigma
    distribution["p_source"] = "beta_predictive_state_empirical_analogs"
    payload["distribution"] = distribution
    payload["state_pq"] = {
        "model_version": state.get("model_version"),
        "regime": state.get("regime"),
        "analog_count": len(outcomes),
        "effective_analogs": float(state.get("effective_analogs") or 0.0),
        "mean_proximity": float(state.get("mean_proximity") or 0.0),
        "analog_mean_bps": analog_mean,
        "target_mean_bps": target_mean_bps,
        "mean_source": mean_source,
        "conformal_scale": conformal,
        "p_big_15": float(state.get("p_big_15") or 0.0),
        "p_persistent_30": float(state.get("p_persistent_30") or 0.0),
        "p_reversal_15": float(state.get("p_reversal_15") or 0.0),
        "p_reversal_30": float(state.get("p_reversal_30") or 0.0),
        "p_acceleration": float(state.get("p_acceleration") or 0.0),
        "q_unchanged": True,
        "strategy_authority": False,
    }
    out["payload"] = payload
    out["expected_return"] = p_mean
    out["sigma_return"] = p_sigma
    out["probability_up"] = p_up
    out["predicted_price"] = spot * (1.0 + p_mean)
    return out


def generate_state_pq_candidates(
    config,
    prediction: dict[str, Any],
    beta_opportunity: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    optimizer_config=None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state_prediction = attach_empirical_state_p(prediction, beta_opportunity)
    candidates = generate_complete_candidates(
        config,
        state_prediction,
        options,
        optimizer_config=optimizer_config,
    )
    for candidate in candidates:
        payload = dict(candidate.get("payload") or {})
        payload.setdefault("state_pq", {})["empirical_p"] = True
        candidate["payload"] = payload
    return state_prediction, candidates


def _drag(candidate: dict[str, Any]) -> float:
    payload = candidate.get("payload") or {}
    v2 = payload.get("v2") or {}
    return float(v2.get("estimated_execution_drag_dollars") or 0.0)


def _pnl_std(candidate: dict[str, Any]) -> float:
    payload = candidate.get("payload") or {}
    return float(payload.get("p_pnl_std") or 0.0)


def _evstd(candidate: dict[str, Any]) -> float:
    return float(candidate.get("expected_value") or 0.0) / max(_pnl_std(candidate), 1e-9)


def _robust_ev(candidate: dict[str, Any]) -> float:
    # Research kill test: 2x spread + natural fills was approximately a 3x
    # baseline quote-drag haircut versus the midpoint-capture convention.
    return float(candidate.get("expected_value") or 0.0) - 3.0 * _drag(candidate)


def _same_legs(a: dict[str, Any], b: dict[str, Any]) -> bool:
    def key(candidate: dict[str, Any]) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            sorted(
                (
                    str(leg.get("symbol") or ""),
                    str(leg.get("side") or ""),
                    int(leg.get("quantity") or 1),
                )
                for leg in candidate.get("legs") or []
            )
        )

    return key(a) == key(b)


def find_primary_valuation(
    primary: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if primary is None:
        return None
    exact = [candidate for candidate in candidates if _same_legs(primary, candidate)]
    if exact:
        return max(exact, key=lambda row: float(row.get("score") or -1e9))
    return None


def state_lane(
    beta_opportunity: dict[str, Any],
    options: list[dict[str, Any]],
) -> tuple[str, float]:
    state = _state(beta_opportunity)
    hgb = beta_opportunity.get("hgb_direction") or {}
    if isinstance(hgb, dict) and bool(hgb.get("eligible")):
        return "DIRECTIONAL_STATE", 1.0
    if state is None or not options:
        return "NO_STATE", 1.0
    spot = float((beta_opportunity.get("snapshot") or {}).get("spy_price") or 0.0)
    iv_rows = [
        row
        for row in options
        if float(row.get("iv") or 0.0) > 0
        and (spot <= 0 or abs(float(row.get("strike") or 0.0) - spot) <= 1.5)
    ]
    if not iv_rows:
        iv_rows = [row for row in options if float(row.get("iv") or 0.0) > 0]
    atm_iv = float(np.median([float(row["iv"]) for row in iv_rows])) if iv_rows else 0.20
    q_sigma_bps = atm_iv * math.sqrt(15.0 / (365.0 * 24.0 * 60.0)) * 10_000.0
    p_sigma_bps = float(state.get("sigma_15") or 0.0) * float(state.get("conformal_scale") or 1.0)
    ratio = p_sigma_bps / max(q_sigma_bps, 1e-9)
    p_big = float(state.get("p_big_15") or 0.0)
    if ratio >= 1.12 and p_big >= 0.50:
        return "EXPANSION_LONGVOL", ratio
    if ratio <= 0.82 and p_big <= 0.38:
        return "COMPRESSION_RANGE", ratio
    return "PQ_DISLOCATION", ratio


def authorize_state_pq_challenger(
    primary: dict[str, Any] | None,
    primary_valuation: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    beta_opportunity: dict[str, Any],
    options: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Champion/challenger authority from the validated state-P/Q replay.

    Every family remains available. Complexity earns authority only when its state-
    conditioned edge survives the same 3x execution-drag haircut that killed the
    unrestricted historical allocator.
    """
    lane, vol_ratio = state_lane(beta_opportunity, options)
    eligible = [candidate for candidate in candidates if candidate.get("status") == "ELIGIBLE"]
    detail: dict[str, Any] = {
        "lane": lane,
        "vol_ratio": vol_ratio,
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "challenger_authorized": False,
    }

    if primary is not None and primary_valuation is not None:
        incumbent_robust = _robust_ev(primary_valuation)
        challengers = [candidate for candidate in eligible if not _same_legs(candidate, primary)]
        qualified = [
            candidate
            for candidate in challengers
            if _robust_ev(candidate) >= incumbent_robust + 4.0
            and float(candidate.get("score") or -1e9)
            >= float(primary_valuation.get("score") or -1e9) + 0.04
            and float(candidate.get("probability_profit") or 0.0)
            >= max(0.55, float(primary_valuation.get("probability_profit") or 0.0) - 0.03)
            and _evstd(candidate) >= max(0.12, _evstd(primary_valuation) - 0.05)
            and _drag(candidate) <= max(8.0, _drag(primary_valuation) + 3.0)
        ]
        if qualified:
            chosen = max(qualified, key=lambda row: float(row.get("score") or -1e9))
            detail.update(
                challenger_authorized=True,
                challenger_family=chosen.get("strategy"),
                challenger_robust_ev=_robust_ev(chosen),
                incumbent_robust_ev=incumbent_robust,
            )
            return chosen, detail
        detail["incumbent_robust_ev"] = incumbent_robust
        return None, detail

    # State-only lanes are allowed to earn authority, but the historical stress
    # test produced no qualifying trades under these thresholds. Keep the code path
    # live so future actual-chain evidence can promote them without architecture work.
    state = _state(beta_opportunity)
    if state is None or float(state.get("effective_analogs") or 0.0) < 30:
        return None, detail
    if lane == "EXPANSION_LONGVOL":
        family_set, ev_min, pop_min, evstd_min = LONG_VOL_FAMILIES, 10.0, 0.58, 0.25
    elif lane == "COMPRESSION_RANGE":
        family_set, ev_min, pop_min, evstd_min = RANGE_FAMILIES, 10.0, 0.60, 0.25
    else:
        family_set, ev_min, pop_min, evstd_min = None, 14.0, 0.62, 0.35
    qualified = [
        candidate
        for candidate in eligible
        if (family_set is None or candidate.get("strategy") in family_set)
        and float(candidate.get("expected_value") or 0.0) >= ev_min
        and _robust_ev(candidate) >= max(5.0, 0.50 * ev_min)
        and float(candidate.get("probability_profit") or 0.0) >= pop_min
        and _evstd(candidate) >= evstd_min
        and float(candidate.get("score") or 0.0) >= 0.12
        and _drag(candidate) <= 8.0
    ]
    if not qualified:
        return None, detail
    chosen = max(qualified, key=lambda row: float(row.get("score") or -1e9))
    detail.update(
        challenger_authorized=True,
        challenger_family=chosen.get("strategy"),
        challenger_robust_ev=_robust_ev(chosen),
    )
    return chosen, detail

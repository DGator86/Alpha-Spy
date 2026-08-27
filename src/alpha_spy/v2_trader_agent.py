from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from .timeutil import ET
from .v2_state_pq import LONG_VOL_FAMILIES, RANGE_FAMILIES

DIRECTIONAL_BULLISH = {
    "LONG_CALL",
    "BULL_CALL_DEBIT_SPREAD",
    "BULL_PUT_CREDIT_SPREAD",
    "CALL_BACKSPREAD_1x2",
    "CALL_BACKSPREAD_1x3",
    "BULLISH_RISK_REVERSAL_WITH_PUT_WING",
}
DIRECTIONAL_BEARISH = {
    "LONG_PUT",
    "BEAR_PUT_DEBIT_SPREAD",
    "BEAR_CALL_CREDIT_SPREAD",
    "PUT_BACKSPREAD_1x2",
    "PUT_BACKSPREAD_1x3",
    "BEARISH_RISK_REVERSAL_WITH_CALL_WING",
}

PLAYBOOK_DIRECTIONAL = "DIRECTIONAL_MOMENTUM"
PLAYBOOK_RANGE = "LATE_RANGE_CARRY"
PLAYBOOK_LONG_VOL = "VOLATILITY_EXPANSION"
PLAYBOOK_MEAN_REVERSION = "MEAN_REVERSION"
PLAYBOOK_TRANSITION = "REGIME_TRANSITION"
PLAYBOOK_RELATIVE_VALUE = "P_Q_RELATIVE_VALUE"
PLAYBOOK_NONE = "NO_EDGE"

ENTRY_NOW = "EXECUTE_NOW"
ENTRY_WAIT_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
ENTRY_WAIT_TRANSITION = "WAIT_FOR_TRANSITION"
ENTRY_WAIT_PRICE = "WAIT_FOR_BETTER_PRICING"


@dataclass(frozen=True)
class TradeThesis:
    thesis_id: str
    created_at: str
    regime: str
    regime_confidence: float
    persistence_15: float
    persistence_30: float
    expected_regime_duration_minutes: float
    successor_probabilities: dict[str, float]
    most_likely_successor: str
    successor_confidence: float
    edge_source: str
    playbook: str
    direction: str
    strategy: str | None
    candidate_id: str | None
    entry_mode: str
    entry_trigger: str
    setup_expires_at: str
    expected_time_to_profit_minutes: float
    first_profit_target_dollars: float
    second_profit_target_dollars: float
    stop_loss_dollars: float
    maximum_loss_dollars: float
    time_stop_minutes: float
    invalidation_conditions: tuple[str, ...]
    adjustment_conditions: tuple[str, ...]
    scale_conditions: tuple[str, ...]
    economics: dict[str, Any]
    evidence_status: str
    setup_key: str

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["invalidation_conditions"] = list(self.invalidation_conditions)
        out["adjustment_conditions"] = list(self.adjustment_conditions)
        out["scale_conditions"] = list(self.scale_conditions)
        return out


@dataclass(frozen=True)
class AgentPlan:
    action: str
    reason: str
    playbook: str
    candidate: dict[str, Any] | None
    thesis: TradeThesis | None
    diagnostics: dict[str, Any]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _regime(beta: dict[str, Any]) -> dict[str, Any]:
    raw = beta.get("regime_forecast") or {}
    if isinstance(raw, dict) and raw:
        return raw
    state = beta.get("predictive_state") or {}
    ready = isinstance(state, dict) and bool(state.get("ready"))
    regime = str(state.get("regime") or "UNDEFINED") if ready else "UNDEFINED"
    pbig15 = _number(state.get("p_big_15"))
    persistent = _number(state.get("p_persistent_30"))
    return {
        "definable": bool(ready and int(state.get("analog_count") or 0) >= 25),
        "current_regime": regime,
        "confidence": min(1.0, _number(state.get("effective_analogs")) / 40.0),
        "persistence_15": 1.0 - _number(state.get("p_reversal_15")),
        "persistence_30": persistent,
        "expected_duration_minutes": 5.0 + 10.0 * (1.0 - _number(state.get("p_reversal_15"))) + 15.0 * persistent,
        "successor_probabilities": {
            "QUIET": max(0.0, 1.0 - pbig15),
            "EXPANSION": pbig15,
        },
        "most_likely_successor": "EXPANSION" if pbig15 > 0.5 else "QUIET",
        "successor_confidence": max(pbig15, 1.0 - pbig15),
    }


def _drag(candidate: dict[str, Any]) -> float:
    return _number(((candidate.get("payload") or {}).get("v2") or {}).get("estimated_execution_drag_dollars"))


def _robust_ev(candidate: dict[str, Any]) -> float:
    return _number(candidate.get("expected_value")) - 3.0 * _drag(candidate)


def _utility(candidate: dict[str, Any]) -> float:
    pop = _number(candidate.get("probability_profit"), 0.5)
    legs = len(candidate.get("legs") or [])
    risk = max(_number(candidate.get("max_loss")), 1.0)
    return _robust_ev(candidate) + 8.0 * (pop - 0.5) - 0.35 * _drag(candidate) - 0.25 * legs - 0.01 * risk


def _eligible(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in candidates if row.get("status") == "ELIGIBLE"]


def _best(candidates: list[dict[str, Any]], families: set[str] | None = None) -> dict[str, Any] | None:
    rows = _eligible(candidates)
    if families is not None:
        rows = [row for row in rows if str(row.get("strategy") or "") in families]
    if not rows:
        return None
    return max(rows, key=_utility)


def _economics(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {}
    v2 = (candidate.get("payload") or {}).get("v2") or {}
    return {
        "expected_value_dollars": _number(candidate.get("expected_value")),
        "robust_ev_after_3x_drag_dollars": _robust_ev(candidate),
        "probability_profit": _number(candidate.get("probability_profit")),
        "maximum_loss_dollars": _number(candidate.get("max_loss")),
        "maximum_profit_dollars": _number(candidate.get("max_profit")),
        "entry_price": _number(candidate.get("entry_price")),
        "entry_kind": candidate.get("entry_kind"),
        "execution_drag_dollars": _drag(candidate),
        "combined_spread": _number(v2.get("combined_spread")),
        "edge_to_execution_drag": _number(v2.get("edge_to_execution_drag")),
        "utility": _utility(candidate),
        "leg_count": len(candidate.get("legs") or []),
    }


def _targets(playbook: str, risk: float) -> tuple[float, float, float, float]:
    risk = max(risk, 1.0)
    if playbook == PLAYBOOK_RANGE:
        return max(5.0, 0.08 * risk), max(18.0, 0.22 * risk), max(10.0, 0.28 * risk), 30.0
    if playbook == PLAYBOOK_LONG_VOL:
        return max(6.0, 0.12 * risk), max(16.0, 0.30 * risk), max(10.0, 0.25 * risk), 20.0
    if playbook == PLAYBOOK_MEAN_REVERSION:
        return max(5.0, 0.10 * risk), max(12.0, 0.25 * risk), max(8.0, 0.24 * risk), 12.0
    if playbook == PLAYBOOK_TRANSITION:
        return max(6.0, 0.12 * risk), max(15.0, 0.28 * risk), max(9.0, 0.25 * risk), 15.0
    return max(5.0, 0.10 * risk), max(15.0, 0.30 * risk), max(9.0, 0.28 * risk), 15.0


def _evidence_status(playbook: str, history: dict[str, Any] | None) -> str:
    history = history or {}
    samples = int(history.get("samples") or 0)
    mean_pnl = _number(history.get("mean_pnl"))
    win_rate = _number(history.get("win_rate"))
    if playbook in {PLAYBOOK_DIRECTIONAL, PLAYBOOK_RANGE} and samples < 10:
        return "RESEARCH_VALIDATED_FORWARD_PENDING"
    if samples >= 20 and mean_pnl < 0:
        return "NARROW_OR_RETIRE"
    if samples >= 12 and mean_pnl > 0 and win_rate >= 0.55:
        return "REPEATABLE"
    if samples >= 8:
        return "PROVISIONAL"
    return "EXPERIMENTAL"


def _setup_key(now: datetime, regime: str, playbook: str, direction: str, successor: str) -> str:
    return "|".join((now.astimezone(ET).date().isoformat(), regime, playbook, direction, successor))


def build_agent_plan(
    beta: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    now: datetime,
    playbook_history: dict[str, dict[str, Any]] | None = None,
) -> AgentPlan:
    """Implement Steps 1-9 of the closed-loop trader decision system.

    Candidate valuation is deliberately downstream of the regime thesis. Positive
    option EV alone never creates a trade. The market must be definable, the edge
    must have a playbook, the entry timing must be appropriate, and the exact
    structure must survive a three-times execution-drag stress test.
    """
    playbook_history = playbook_history or {}
    regime = _regime(beta)
    current = str(regime.get("current_regime") or "UNDEFINED")
    confidence = _number(regime.get("confidence"))
    p15 = _number(regime.get("persistence_15"))
    p30 = _number(regime.get("persistence_30"))
    duration = _number(regime.get("expected_duration_minutes"))
    successors = regime.get("successor_probabilities") or {}
    if not isinstance(successors, dict):
        successors = {}
    successor = str(regime.get("most_likely_successor") or "UNDEFINED")
    successor_conf = _number(regime.get("successor_confidence"))
    state = beta.get("predictive_state") or {}
    hgb = beta.get("hgb_direction") or {}
    local = now.astimezone(ET)

    diagnostics = {
        "regime": current,
        "regime_confidence": confidence,
        "persistence_15": p15,
        "persistence_30": p30,
        "expected_duration_minutes": duration,
        "successor_probabilities": successors,
        "most_likely_successor": successor,
        "successor_confidence": successor_conf,
        "candidate_count": len(candidates),
    }

    if not bool(regime.get("definable")) or confidence < 0.40:
        return AgentPlan("NO_TRADE", "regime_not_definable", PLAYBOOK_NONE, None, None, diagnostics)
    if duration < 8.0:
        return AgentPlan("NO_TRADE", "regime_duration_too_uncertain", PLAYBOOK_NONE, None, None, diagnostics)

    playbook = PLAYBOOK_NONE
    direction = "NEUTRAL"
    edge_source = "none"
    entry_mode = ENTRY_NOW
    entry_trigger = "edge_and_execution_conditions_already_satisfied"
    candidate: dict[str, Any] | None = None

    hgb_eligible = isinstance(hgb, dict) and bool(hgb.get("eligible"))
    hgb_direction = str(hgb.get("direction") or "") if isinstance(hgb, dict) else ""
    hgb_strength = _number(hgb.get("strength")) if isinstance(hgb, dict) else 0.0
    pbig15 = _number(state.get("p_big_15")) if isinstance(state, dict) else 0.0
    reversal15 = _number(state.get("p_reversal_15")) if isinstance(state, dict) else 0.0

    if hgb_eligible and hgb_strength >= 0.35:
        playbook = PLAYBOOK_DIRECTIONAL
        direction = "BULLISH" if hgb_direction == "BULLISH" else "BEARISH"
        edge_source = "validated_hgb_direction_plus_state_distribution"
        families = DIRECTIONAL_BULLISH if direction == "BULLISH" else DIRECTIONAL_BEARISH
        candidate = _best(candidates, families)
        if candidate is None:
            return AgentPlan("WAIT", "directional_edge_but_no_efficient_structure", playbook, None, None, diagnostics)
        if p15 < 0.40:
            entry_mode = ENTRY_WAIT_CONFIRMATION
            entry_trigger = "regime_persistence_15_must_recover_above_0.40"
    elif current == "QUIET" and p30 >= 0.72 and pbig15 <= 0.38:
        playbook = PLAYBOOK_RANGE
        edge_source = "persistent_quiet_state_plus_option_carry"
        if local.hour < 14 or (local.hour == 14 and local.minute < 45):
            entry_mode = ENTRY_WAIT_PRICE
            entry_trigger = "late_session_range_window_14:45_ET_or_later"
        if local.hour > 15 or (local.hour == 15 and local.minute > 15):
            return AgentPlan("NO_TRADE", "insufficient_time_for_range_playbook", playbook, None, None, diagnostics)
        candidate = _best(candidates, RANGE_FAMILIES)
    elif current == "EXPANSION" and pbig15 >= 0.50:
        playbook = PLAYBOOK_LONG_VOL
        edge_source = "current_volatility_expansion"
        candidate = _best(candidates, LONG_VOL_FAMILIES)
    elif current in {"DIRECTIONAL_UP", "DIRECTIONAL_DOWN"} and reversal15 >= 0.58:
        playbook = PLAYBOOK_MEAN_REVERSION
        direction = "BEARISH" if current == "DIRECTIONAL_UP" else "BULLISH"
        edge_source = "high_state_conditioned_reversal_probability"
        families = DIRECTIONAL_BEARISH if direction == "BEARISH" else DIRECTIONAL_BULLISH
        candidate = _best(candidates, families)
        entry_mode = ENTRY_WAIT_CONFIRMATION
        entry_trigger = "reversal_regime_or_opposite_hgb_confirmation"
    elif successor in {"DIRECTIONAL_UP", "DIRECTIONAL_DOWN", "EXPANSION"} and successor_conf >= 0.38:
        playbook = PLAYBOOK_TRANSITION
        direction = "BULLISH" if successor == "DIRECTIONAL_UP" else "BEARISH" if successor == "DIRECTIONAL_DOWN" else "NEUTRAL"
        edge_source = "forecast_successor_regime"
        if successor == "EXPANSION":
            candidate = _best(candidates, LONG_VOL_FAMILIES)
        else:
            families = DIRECTIONAL_BULLISH if direction == "BULLISH" else DIRECTIONAL_BEARISH
            candidate = _best(candidates, families)
        entry_mode = ENTRY_WAIT_TRANSITION
        entry_trigger = f"current_regime_must_transition_to_{successor}"
    else:
        best_any = _best(candidates)
        if best_any is not None and _robust_ev(best_any) >= 12.0 and _number(best_any.get("probability_profit")) >= 0.62:
            playbook = PLAYBOOK_RELATIVE_VALUE
            edge_source = "large_state_conditioned_P_minus_Q_dislocation"
            candidate = best_any
        else:
            return AgentPlan("NO_TRADE", "no_monetizable_current_or_transition_edge", PLAYBOOK_NONE, None, None, diagnostics)

    if candidate is None:
        return AgentPlan("WAIT", "edge_identified_but_no_efficient_instrument", playbook, None, None, diagnostics)

    economics = _economics(candidate)
    robust_ev = _number(economics.get("robust_ev_after_3x_drag_dollars"))
    pop = _number(economics.get("probability_profit"))
    risk = _number(economics.get("maximum_loss_dollars"))
    min_edge = 2.0 if playbook == PLAYBOOK_DIRECTIONAL else 5.0 if playbook in {PLAYBOOK_RANGE, PLAYBOOK_LONG_VOL} else 8.0
    min_pop = 0.54 if playbook == PLAYBOOK_DIRECTIONAL else 0.57
    if risk <= 0.0 or risk > 100.0:
        return AgentPlan("NO_TRADE", "instrument_risk_not_tolerable", playbook, None, None, diagnostics)
    if robust_ev < min_edge or pop < min_pop:
        return AgentPlan("WAIT", "strategy_edge_does_not_clear_cost_hurdle", playbook, None, None, {**diagnostics, "economics": economics})

    first, second, stop, expected_time = _targets(playbook, risk)
    time_stop = min(max(expected_time * 1.35, expected_time + 5.0), max(duration, expected_time + 5.0))
    setup_life = 5.0 if playbook == PLAYBOOK_DIRECTIONAL else 10.0 if playbook != PLAYBOOK_RANGE else 15.0
    successor_probs = {str(k): _number(v) for k, v in successors.items()}
    evidence = _evidence_status(playbook, playbook_history.get(playbook))
    setup_key = _setup_key(now, current, playbook, direction, successor)

    invalidation = [
        "regime_becomes_undefined",
        "regime_thesis_probability_collapses",
        "better_forward_risk_reward_no_longer_compensates_for_hold",
        "maximum_loss_or_playbook_stop_reached",
    ]
    if playbook == PLAYBOOK_DIRECTIONAL:
        invalidation.extend(("validated_hgb_flips_direction", "successor_probability_shifts_to_opposite_direction"))
    elif playbook == PLAYBOOK_RANGE:
        invalidation.extend(("p_big_15_rises_above_0.50", "implied_volatility_rises_5_points_from_entry", "regime_changes_to_expansion_or_directional"))
    elif playbook == PLAYBOOK_LONG_VOL:
        invalidation.extend(("p_big_15_collapses_below_0.35", "implied_volatility_collapses_5_points_without_underlying_move"))

    thesis = TradeThesis(
        thesis_id=f"TH-{uuid.uuid4().hex[:16]}",
        created_at=now.isoformat(),
        regime=current,
        regime_confidence=confidence,
        persistence_15=p15,
        persistence_30=p30,
        expected_regime_duration_minutes=duration,
        successor_probabilities=successor_probs,
        most_likely_successor=successor,
        successor_confidence=successor_conf,
        edge_source=edge_source,
        playbook=playbook,
        direction=direction,
        strategy=str(candidate.get("strategy") or ""),
        candidate_id=str(candidate.get("candidate_id") or ""),
        entry_mode=entry_mode,
        entry_trigger=entry_trigger,
        setup_expires_at=(now + timedelta(minutes=setup_life)).isoformat(),
        expected_time_to_profit_minutes=expected_time,
        first_profit_target_dollars=first,
        second_profit_target_dollars=second,
        stop_loss_dollars=stop,
        maximum_loss_dollars=risk,
        time_stop_minutes=time_stop,
        invalidation_conditions=tuple(invalidation),
        adjustment_conditions=(
            "thesis_valid_but_current_structure_no_longer_best_expression",
            "greek_or_iv_exposure_deteriorates_relative_to_alternative_structure",
        ),
        scale_conditions=(
            "first_profit_target_reached",
            "thesis_remains_valid",
            "regime_persistence_not_deteriorating",
        ),
        economics=economics,
        evidence_status=evidence,
        setup_key=setup_key,
    )
    diagnostics = {**diagnostics, "economics": economics, "evidence_status": evidence}

    if entry_mode != ENTRY_NOW:
        return AgentPlan("WAIT", "valid_edge_waiting_for_entry_trigger", playbook, candidate, thesis, diagnostics)
    return AgentPlan("ENTER", "complete_trade_thesis_and_economics_pass", playbook, candidate, thesis, diagnostics)

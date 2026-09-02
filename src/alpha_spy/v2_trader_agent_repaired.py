from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any

from . import v2_trader_agent as legacy
from .timeutil import ET

_HGB_AUTHORITY = "beta_v2_hgb_blocked_walk_forward"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _successor_authority(market: dict[str, Any]) -> bool:
    lifecycle = market.get("lifecycle") or {}
    calibration = lifecycle.get("calibration") or {} if isinstance(lifecycle, dict) else {}
    return bool(calibration.get("successor_authority")) if isinstance(calibration, dict) else False


def _sanitized_market(market: dict[str, Any]) -> dict[str, Any]:
    """Demote immature successor direction and consumed HGB signals.

    Step 4 remains visible in diagnostics, but it cannot hard-veto or authorize a
    trade until scored transition calibration has earned that authority. Likewise,
    once the validated directional setup has traded for the session, later HGB
    prints remain evidence/monitoring telemetry rather than fresh opportunities.
    """
    out = deepcopy(market)
    regime = out.get("regime_forecast") or {}
    if isinstance(regime, dict) and not _successor_authority(out):
        regime["successor_advisory"] = {
            "most_likely_successor": regime.get("most_likely_successor"),
            "successor_confidence": regime.get("successor_confidence"),
            "successor_probabilities": regime.get("successor_probabilities"),
        }
        regime["most_likely_successor"] = "UNDEFINED"
        regime["successor_confidence"] = 0.0
        regime["successor_probabilities"] = {}
        out["regime_forecast"] = regime

    if bool(out.get("directional_setup_used_today")):
        hgb = out.get("hgb_direction") or {}
        if isinstance(hgb, dict):
            hgb = dict(hgb)
            hgb["eligible"] = False
            hgb["consumed_for_new_entry"] = True
            out["hgb_direction"] = hgb
    return out


def _hgb_control(candidates: list[dict[str, Any]], direction: str) -> dict[str, Any] | None:
    bullish = direction == "BULLISH"
    expected = "CALL_DEBIT_SPREAD" if bullish else "PUT_DEBIT_SPREAD"
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        payload = candidate.get("payload") or {}
        if str(payload.get("authority") or "") != _HGB_AUTHORITY:
            continue
        if str(candidate.get("strategy") or "") != expected:
            continue
        risk = _num(candidate.get("max_loss"))
        if risk <= 0.0 or risk > 100.0:
            continue
        rows.append(candidate)
    if not rows:
        return None
    return min(
        rows,
        key=lambda row: (
            _num((row.get("payload") or {}).get("execution", {}).get("estimated_execution_drag_dollars"), 999.0),
            _num(row.get("max_loss"), 999.0),
        ),
    )


def _directional_plan(
    market: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    now,
    playbook_history: dict[str, dict[str, Any]],
) -> legacy.AgentPlan | None:
    regime = market.get("regime_forecast") or {}
    hgb = market.get("hgb_direction") or {}
    if not isinstance(regime, dict) or not isinstance(hgb, dict):
        return None
    if bool(market.get("directional_setup_used_today")):
        return None
    if not bool(hgb.get("eligible")) or _num(hgb.get("strength")) < 0.35:
        return None

    current = str(regime.get("current_regime") or "UNDEFINED")
    confidence = _num(regime.get("confidence"))
    duration = _num(regime.get("expected_duration_minutes"))
    p15 = _num(regime.get("persistence_15"))
    p30 = _num(regime.get("persistence_30"))
    successors = regime.get("successor_probabilities") or {}
    if not isinstance(successors, dict):
        successors = {}
    successor = str(regime.get("most_likely_successor") or "UNDEFINED")
    successor_conf = _num(regime.get("successor_confidence"))

    diagnostics = {
        "regime": current,
        "regime_confidence": confidence,
        "regime_authority": market.get("regime_authority"),
        "lifecycle_authority": market.get("lifecycle_authority"),
        "lifecycle_source": regime.get("source"),
        "persistence_15": p15,
        "persistence_30": p30,
        "expected_duration_minutes": duration,
        "successor_probabilities": successors,
        "most_likely_successor": successor,
        "successor_confidence": successor_conf,
        "successor_authority": _successor_authority(market),
        "directional_entry_authority": "first_qualifying_hgb_setup_per_session",
        "legacy_pq_ev_authority": False,
    }
    if not bool(regime.get("definable")) or confidence < 0.40:
        return legacy.AgentPlan(
            "NO_TRADE",
            "regime_or_lifecycle_not_definable",
            legacy.PLAYBOOK_NONE,
            None,
            None,
            diagnostics,
        )
    if duration < 8.0:
        return legacy.AgentPlan(
            "NO_TRADE",
            "regime_duration_too_uncertain",
            legacy.PLAYBOOK_NONE,
            None,
            None,
            diagnostics,
        )

    direction = str(hgb.get("direction") or "")
    if direction not in {"BULLISH", "BEARISH"}:
        return None
    candidate = _hgb_control(candidates, direction)
    if candidate is None:
        return legacy.AgentPlan(
            "WAIT",
            "validated_directional_edge_but_no_liquid_control_geometry",
            legacy.PLAYBOOK_DIRECTIONAL,
            None,
            None,
            diagnostics,
        )

    governance = playbook_history.get(legacy.PLAYBOOK_DIRECTIONAL, {})
    evidence = legacy._evidence_status(governance)
    if evidence in {"NARROW_OR_RETIRE", "RETIRED"}:
        return legacy.AgentPlan(
            "NO_TRADE",
            "playbook_governance_block",
            legacy.PLAYBOOK_DIRECTIONAL,
            None,
            None,
            {**diagnostics, "playbook_governance": governance},
        )

    risk = _num(candidate.get("max_loss"))
    execution = (candidate.get("payload") or {}).get("execution") or {}
    drag = _num(execution.get("estimated_execution_drag_dollars"))
    hit_probability = _num(
        (candidate.get("payload") or {}).get("signal_hit_probability"),
        0.5,
    )
    economics = {
        "validation_authority": _HGB_AUTHORITY,
        "legacy_pq_ev_authority": False,
        "expected_value_dollars": None,
        "robust_ev_after_3x_drag_dollars": None,
        "probability_profit": hit_probability,
        "maximum_loss_dollars": risk,
        "maximum_profit_dollars": _num(candidate.get("max_profit")),
        "entry_price": _num(candidate.get("entry_price")),
        "entry_kind": candidate.get("entry_kind"),
        "execution_drag_dollars": drag,
        "combined_spread": _num(execution.get("combined_spread")),
        "leg_count": len(candidate.get("legs") or []),
        "signal_strength": _num(hgb.get("strength")),
        "signal_expected_return_bps": _num(hgb.get("expected_return_bps")),
    }
    first, second, stop, expected_time = legacy._targets(
        legacy.PLAYBOOK_DIRECTIONAL,
        risk,
    )
    # Preserve the independently validated ~15m directional monetization horizon.
    # Lifecycle is allowed to invalidate a deteriorating thesis, but immature
    # successor direction and synthetic P/Q EV cannot revoke the entry.
    time_stop = max(expected_time, min(max(duration, expected_time), 20.0))
    setup_key = "|".join(
        (
            now.astimezone(ET).date().isoformat(),
            legacy.PLAYBOOK_DIRECTIONAL,
            "FIRST_SETUP",
        )
    )
    thesis = legacy.TradeThesis(
        thesis_id=f"TH-{__import__('uuid').uuid4().hex[:16]}",
        created_at=now.isoformat(),
        regime=current,
        regime_confidence=confidence,
        persistence_15=p15,
        persistence_30=p30,
        expected_regime_duration_minutes=duration,
        successor_probabilities={str(k): _num(v) for k, v in successors.items()},
        most_likely_successor=successor,
        successor_confidence=successor_conf,
        edge_source="validated_first_hgb_direction_plus_alpha_lifecycle_context",
        playbook=legacy.PLAYBOOK_DIRECTIONAL,
        direction=direction,
        strategy=str(candidate.get("strategy") or ""),
        candidate_id=str(candidate.get("candidate_id") or ""),
        entry_mode=legacy.ENTRY_NOW,
        entry_trigger="first_qualifying_hgb_setup_and_lifecycle_defined",
        setup_expires_at=(now + timedelta(minutes=5)).isoformat(),
        expected_time_to_profit_minutes=expected_time,
        first_profit_target_dollars=first,
        second_profit_target_dollars=second,
        stop_loss_dollars=stop,
        maximum_loss_dollars=risk,
        time_stop_minutes=time_stop,
        invalidation_conditions=(
            "regime_becomes_undefined",
            "validated_hgb_flips_direction",
            "alpha_lifecycle_probability_collapses",
            "maximum_loss_or_playbook_stop_reached",
        ),
        adjustment_conditions=(
            "thesis_valid_but_current_structure_no_longer_best_expression",
        ),
        scale_conditions=(
            "first_profit_target_reached",
            "thesis_remains_valid",
            "alpha_regime_persistence_not_deteriorating",
        ),
        economics=economics,
        evidence_status=evidence,
        setup_key=setup_key,
    )
    candidate_payload = dict(candidate.get("payload") or {})
    candidate_payload["directional_control_lane"] = True
    candidate_payload["legacy_pq_ev_authority"] = False
    candidate["payload"] = candidate_payload
    return legacy.AgentPlan(
        "ENTER",
        "validated_directional_control_lane",
        legacy.PLAYBOOK_DIRECTIONAL,
        candidate,
        thesis,
        {
            **diagnostics,
            "economics": economics,
            "evidence_status": evidence,
            "playbook_governance": governance,
        },
    )


def build_agent_plan(
    market: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    now,
    playbook_history: dict[str, dict[str, Any]] | None = None,
) -> legacy.AgentPlan:
    """Replay-repaired Step 5 opportunity authority.

    1. A validated HGB signal is a *session setup*, not a trade every five minutes.
    2. The HGB $2 control lane is authorized by its blocked walk-forward evidence;
       synthetic/state P-Q EV is telemetry/challenger evidence, not a veto.
    3. Successor direction remains advisory until its own OOS calibration earns
       authority. Other playbooks continue through the existing cost/edge engine.
    """
    history = playbook_history or {}
    directional = _directional_plan(
        market,
        candidates,
        now=now,
        playbook_history=history,
    )
    if directional is not None:
        return directional

    sanitized = _sanitized_market(market)
    return legacy.build_agent_plan(
        sanitized,
        candidates,
        now=now,
        playbook_history=history,
    )

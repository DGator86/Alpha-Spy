from __future__ import annotations

import json
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def playbook_history(journal, *, limit: int = 400) -> dict[str, dict[str, Any]]:
    """Summarize only already-closed trades; never use open/future outcomes."""
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT realized_pnl,payload_json
            FROM positions
            WHERE status='CLOSED' AND realized_pnl IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    grouped: dict[str, list[float]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        candidate = payload.get("candidate") or {}
        inner = candidate.get("payload") or {}
        thesis = inner.get("trade_thesis") or payload.get("trade_thesis") or {}
        playbook = str(thesis.get("playbook") or "") if isinstance(thesis, dict) else ""
        if not playbook:
            continue
        grouped.setdefault(playbook, []).append(_num(row["realized_pnl"]))

    out: dict[str, dict[str, Any]] = {}
    for playbook, pnl in grouped.items():
        samples = len(pnl)
        out[playbook] = {
            "samples": samples,
            "mean_pnl": sum(pnl) / max(samples, 1),
            "win_rate": sum(value > 0 for value in pnl) / max(samples, 1),
            "net_pnl": sum(pnl),
        }
    return out


def post_trade_review(position: dict[str, Any], *, exit_reason: str | None = None) -> dict[str, Any]:
    """Steps 14-16: separate process quality from realized variance."""
    payload = position.get("payload") or {}
    candidate = payload.get("candidate") or {}
    inner = candidate.get("payload") or {}
    thesis = inner.get("trade_thesis") or payload.get("trade_thesis") or {}
    management = payload.get("management_state") or {}
    pnl = _num(position.get("realized_pnl"))
    regime_conf = _num(thesis.get("regime_confidence")) if isinstance(thesis, dict) else 0.0
    robust_ev = _num((thesis.get("economics") or {}).get("robust_ev_after_3x_drag_dollars")) if isinstance(thesis, dict) else 0.0
    thesis_valid_at_exit = bool(management.get("thesis_valid")) if isinstance(management, dict) else False
    reason = str(exit_reason or position.get("exit_reason") or "unknown")

    process_checks = {
        "regime_identification": regime_conf >= 0.40,
        "duration_forecast": _num(thesis.get("expected_regime_duration_minutes")) >= 8.0 if isinstance(thesis, dict) else False,
        "transition_forecast": bool(thesis.get("most_likely_successor")) if isinstance(thesis, dict) else False,
        "edge_quality": robust_ev > 0.0,
        "strategy_selection": bool(thesis.get("strategy")) if isinstance(thesis, dict) else False,
        "entry_timing": str(thesis.get("entry_mode") or "") == "EXECUTE_NOW" if isinstance(thesis, dict) else False,
        "cost_assumptions": _num((thesis.get("economics") or {}).get("execution_drag_dollars")) >= 0.0 if isinstance(thesis, dict) else False,
        "risk_management": _num(thesis.get("stop_loss_dollars")) > 0.0 if isinstance(thesis, dict) else False,
        "exit_execution": bool(reason),
    }
    process_score = sum(process_checks.values()) / max(len(process_checks), 1)
    good_process = process_score >= 0.78
    profitable = pnl > 0
    if good_process and profitable:
        attribution = "GOOD_PROCESS_FAVORABLE_OUTCOME"
    elif good_process and not profitable:
        attribution = "GOOD_PROCESS_UNFAVORABLE_VARIANCE_OR_FORECAST_ERROR"
    elif not good_process and profitable:
        attribution = "BAD_OR_INCOMPLETE_PROCESS_FAVORABLE_VARIANCE"
    else:
        attribution = "BAD_OR_INCOMPLETE_PROCESS_UNFAVORABLE_OUTCOME"

    lessons: list[str] = []
    if reason == "trade_failed_on_expected_time_to_profit":
        lessons.append("expected_time_to_profit_was_not_met")
    if reason in {"directional_thesis_flipped", "range_regime_broke", "forecast_successor_regime_changed"}:
        lessons.append("regime_or_transition_forecast_invalidated_trade")
    if reason == "implied_volatility_expanded_five_points":
        lessons.append("short_vol_expression_failed_on_iv_expansion")
    if profitable and thesis_valid_at_exit:
        lessons.append("playbook_monetized_with_thesis_intact")
    if not lessons:
        lessons.append("collect_more_examples_before_rule_change")

    return {
        "playbook": thesis.get("playbook") if isinstance(thesis, dict) else None,
        "strategy": thesis.get("strategy") if isinstance(thesis, dict) else None,
        "realized_pnl": pnl,
        "exit_reason": reason,
        "process_checks": process_checks,
        "process_score": process_score,
        "good_process": good_process,
        "profitable": profitable,
        "attribution": attribution,
        "lessons": lessons,
        "repeatability_policy": "record_then_require_independent_recurrence_before_rule_change",
    }

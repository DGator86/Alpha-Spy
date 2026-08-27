from __future__ import annotations

import json
import math
from typing import Any

from .v2_playbook_governance import evaluate_playbooks


def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def playbook_history(journal, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
    """Compatibility entry point backed by the Step-16 governance engine."""
    return evaluate_playbooks(journal, limit=limit)


def _component(status: str, evidence: str, **detail: Any) -> dict[str, Any]:
    return {"status": status, "evidence": evidence, **detail}


def _lifecycle_score(journal, forecast_id: str | None) -> dict[str, Any] | None:
    if journal is None or not forecast_id:
        return None
    try:
        with journal.session() as con:
            row = con.execute(
                "SELECT score_json FROM v2_lifecycle_forecasts WHERE forecast_id=?",
                (str(forecast_id),),
            ).fetchone()
    except Exception:
        return None
    if not row or not row["score_json"]:
        return None
    try:
        score = json.loads(row["score_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return score if isinstance(score, dict) else None


def _counterfactuals(journal, decision_id: str | None) -> list[dict[str, Any]]:
    if journal is None or not decision_id:
        return []
    try:
        with journal.session() as con:
            row = con.execute(
                "SELECT prediction_id FROM decisions WHERE decision_id=?",
                (str(decision_id),),
            ).fetchone()
            if not row:
                return []
            rows = con.execute(
                """
                SELECT c.candidate_id,c.strategy,c.expected_value,c.status,o.pnl,o.mfe,o.mae
                FROM candidates c
                JOIN candidate_outcomes o ON o.candidate_id=c.candidate_id
                WHERE c.prediction_id=? AND o.pnl IS NOT NULL
                """,
                (row["prediction_id"],),
            ).fetchall()
    except Exception:
        return []
    return [dict(item) for item in rows]


def _entry_cost_error(journal, position: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
    if journal is None or not position.get("decision_id"):
        return None
    try:
        with journal.session() as con:
            row = con.execute(
                """
                SELECT average_fill_price,quantity FROM orders
                WHERE decision_id=? AND average_fill_price IS NOT NULL
                ORDER BY created_at ASC LIMIT 1
                """,
                (str(position["decision_id"]),),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    expected = _num(candidate.get("entry_price"), -1.0)
    actual = _num(row["average_fill_price"], -1.0)
    if expected < 0.0 or actual < 0.0:
        return None
    quantity = max(1, int(row["quantity"] or position.get("quantity") or 1))
    return {
        "expected_entry_price": expected,
        "actual_entry_price": actual,
        "absolute_entry_slippage_dollars": abs(actual - expected) * 100.0 * quantity,
    }


def post_trade_review(
    position: dict[str, Any],
    *,
    exit_reason: str | None = None,
    journal=None,
) -> dict[str, Any]:
    """Steps 14-15: attribute the outcome to the component that actually failed.

    Profit is never used as a blanket process grade. Components without an
    observable counterfactual or matured target remain UNKNOWN rather than being
    silently counted as correct.
    """
    payload = position.get("payload") or {}
    candidate = payload.get("candidate") or {}
    inner = candidate.get("payload") or {}
    thesis = inner.get("trade_thesis") or payload.get("trade_thesis") or {}
    if not isinstance(thesis, dict):
        thesis = {}
    management = payload.get("management_state") or {}
    if not isinstance(management, dict):
        management = {}
    pnl = _num(position.get("realized_pnl"))
    reason = str(exit_reason or position.get("exit_reason") or "unknown")
    lifecycle = _lifecycle_score(journal, thesis.get("lifecycle_forecast_id"))
    counterfactuals = _counterfactuals(journal, position.get("decision_id"))
    cost_error = _entry_cost_error(journal, position, candidate)

    components: dict[str, dict[str, Any]] = {}
    components["regime_identification"] = _component(
        "UNKNOWN",
        "regime_is_latent_no_independent_ground_truth_label",
        alpha_regime=thesis.get("regime"),
        hierarchy=thesis.get("alpha_regime"),
    )

    if lifecycle is None:
        components["duration_forecast"] = _component(
            "UNKNOWN", "lifecycle_forecast_not_matured_or_not_recorded"
        )
        components["transition_forecast"] = _component(
            "UNKNOWN", "successor_outcome_not_matured_or_not_recorded"
        )
    else:
        expected = _num(thesis.get("expected_regime_duration_minutes"))
        actual_remaining = lifecycle.get("actual_remaining_minutes")
        censored = bool(lifecycle.get("duration_censored_at_30m"))
        if actual_remaining is None and censored:
            p30 = _num((thesis.get("lifecycle") or {}).get("persistence_30"))
            duration_pass = p30 >= 0.50 or expected >= 25.0
            components["duration_forecast"] = _component(
                "PASS" if duration_pass else "FAIL",
                "regime_survived_entire_30m_scoring_window",
                expected_remaining_minutes=expected,
                censored_at_30m=True,
                predicted_persistence_30=p30,
            )
        elif actual_remaining is not None:
            error = abs(expected - _num(actual_remaining))
            tolerance = max(5.0, 0.35 * max(expected, 1.0))
            components["duration_forecast"] = _component(
                "PASS" if error <= tolerance else "FAIL",
                "matured_regime_episode_duration",
                expected_remaining_minutes=expected,
                actual_remaining_minutes=_num(actual_remaining),
                absolute_error_minutes=error,
                tolerance_minutes=tolerance,
            )
        else:
            components["duration_forecast"] = _component("UNKNOWN", "duration_target_unavailable")

        actual_successor = lifecycle.get("actual_successor")
        transition_correct = lifecycle.get("transition_correct")
        if actual_successor is None or transition_correct is None:
            components["transition_forecast"] = _component(
                "UNKNOWN", "no_regime_transition_observed_inside_scoring_window"
            )
        else:
            components["transition_forecast"] = _component(
                "PASS" if bool(transition_correct) else "FAIL",
                "first_observed_successor_regime",
                predicted_successor=thesis.get("most_likely_successor"),
                actual_successor=actual_successor,
                assigned_probability=lifecycle.get("actual_successor_probability"),
            )

    robust_ev = _num((thesis.get("economics") or {}).get("robust_ev_after_3x_drag_dollars"))
    components["edge_quality"] = _component(
        "UNKNOWN",
        "single_realized_trade_cannot_establish_or_disprove_edge",
        forecast_robust_ev=robust_ev,
        realized_pnl=pnl,
    )

    selected_id = str(candidate.get("candidate_id") or "")
    selected_cf = next(
        (row for row in counterfactuals if str(row.get("candidate_id") or "") == selected_id),
        None,
    )
    if selected_cf is None or len(counterfactuals) < 2:
        components["strategy_selection"] = _component(
            "UNKNOWN", "comparable_shadow_candidate_outcomes_not_available"
        )
        components["option_structure"] = _component(
            "UNKNOWN", "same-timestamp_counterfactual_option_outcomes_not_available"
        )
    else:
        best = max(counterfactuals, key=lambda row: _num(row.get("pnl"), -1e9))
        selected_pnl = _num(selected_cf.get("pnl"))
        best_pnl = _num(best.get("pnl"))
        regret = best_pnl - selected_pnl
        pass_selection = regret <= max(3.0, 0.25 * max(abs(best_pnl), 1.0))
        components["strategy_selection"] = _component(
            "PASS" if pass_selection else "FAIL",
            "same_prediction_candidate_outcome_regret",
            selected_strategy=selected_cf.get("strategy"),
            selected_counterfactual_pnl=selected_pnl,
            best_strategy=best.get("strategy"),
            best_counterfactual_pnl=best_pnl,
            regret_dollars=regret,
        )
        components["option_structure"] = _component(
            "PASS" if pass_selection else "FAIL",
            "selected_structure_compared_with_same_timestamp_bounded_risk_alternatives",
            regret_dollars=regret,
        )

    components["entry_timing"] = _component(
        "UNKNOWN",
        "entry_time_counterfactual_requires_nearby_state_replay",
        entry_mode=thesis.get("entry_mode"),
        entry_trigger=thesis.get("entry_trigger"),
    )

    modeled_drag = _num((thesis.get("economics") or {}).get("execution_drag_dollars"))
    if cost_error is None:
        components["cost_assumptions"] = _component(
            "UNKNOWN", "actual_entry_fill_not_available_for_cost_comparison", modeled_drag_dollars=modeled_drag
        )
    else:
        actual_slippage = _num(cost_error.get("absolute_entry_slippage_dollars"))
        tolerance = max(1.0, modeled_drag)
        components["cost_assumptions"] = _component(
            "PASS" if actual_slippage <= tolerance else "FAIL",
            "actual_entry_slippage_vs_modeled_execution_drag",
            modeled_drag_dollars=modeled_drag,
            **cost_error,
        )

    max_loss = _num(thesis.get("maximum_loss_dollars"), _num(position.get("max_loss")))
    stop = _num(thesis.get("stop_loss_dollars"))
    bounded = max_loss > 0.0 and pnl >= -max_loss - 1e-6
    components["risk_management"] = _component(
        "PASS" if bounded and stop > 0.0 else "FAIL",
        "bounded_loss_and_predefined_invalidation",
        maximum_loss_dollars=max_loss,
        stop_loss_dollars=stop,
        realized_pnl=pnl,
    )

    mfe = _num(position.get("mfe"))
    capture = pnl / mfe if mfe > 1e-9 else None
    if capture is None:
        components["exit_execution"] = _component(
            "UNKNOWN", "no_positive_mfe_available_for_exit_capture_analysis", exit_reason=reason
        )
    else:
        components["exit_execution"] = _component(
            "PASS" if capture >= 0.35 else "FAIL",
            "realized_pnl_as_fraction_of_max_favorable_excursion",
            exit_reason=reason,
            mfe=mfe,
            realized_pnl=pnl,
            capture_ratio=capture,
        )

    known = [row for row in components.values() if row["status"] in {"PASS", "FAIL"}]
    passed = sum(row["status"] == "PASS" for row in known)
    process_score = passed / len(known) if known else 0.0
    failed_components = [name for name, row in components.items() if row["status"] == "FAIL"]
    good_process = bool(known and process_score >= 0.75)
    profitable = pnl > 0.0

    if good_process and profitable:
        attribution = "GOOD_PROCESS_FAVORABLE_OUTCOME"
    elif good_process and not profitable:
        attribution = "GOOD_PROCESS_UNFAVORABLE_VARIANCE_OR_UNMEASURED_EDGE_ERROR"
    elif not good_process and profitable:
        attribution = "PROCESS_DEFECT_OR_UNCERTAINTY_WITH_FAVORABLE_OUTCOME"
    else:
        attribution = "PROCESS_DEFECT_OR_UNCERTAINTY_WITH_UNFAVORABLE_OUTCOME"

    lessons = [f"inspect_{name}" for name in failed_components]
    if not lessons:
        lessons.append("no_component_rule_change_until_repeatable_evidence")

    return {
        "playbook": thesis.get("playbook"),
        "strategy": thesis.get("strategy"),
        "realized_pnl": pnl,
        "exit_reason": reason,
        "component_attribution": components,
        "known_component_count": len(known),
        "failed_components": failed_components,
        "primary_failure_component": failed_components[0] if failed_components else None,
        "process_score": process_score,
        "good_process": good_process,
        "profitable": profitable,
        "attribution": attribution,
        "lessons": lessons,
        "repeatability_policy": "component_specific_errors_feed_research_challengers_not_live_self_mutation",
    }


def refresh_closed_trade_reviews(journal, *, limit: int = 100) -> int:
    """Backfill reviews after lifecycle/counterfactual outcomes mature."""
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT * FROM positions WHERE status='CLOSED' AND realized_pnl IS NOT NULL
            ORDER BY closed_at DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    updated = 0
    for raw in rows:
        position = dict(raw)
        try:
            position["legs"] = json.loads(position.pop("legs_json") or "[]")
            position["payload"] = json.loads(position.pop("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        review = post_trade_review(position, exit_reason=position.get("exit_reason"), journal=journal)
        old = (position.get("payload") or {}).get("post_trade_review")
        if old == review:
            continue
        position.setdefault("payload", {})["post_trade_review"] = review
        journal.upsert_position(position)
        updated += 1
    return updated

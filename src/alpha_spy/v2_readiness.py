from __future__ import annotations

import json
import math
from typing import Any

from .v2_playbook_governance import evaluate_playbooks
from .v2_policy import CURRENT_POLICY_VERSION, POLICY_CONTRACT


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def lifecycle_calibration(journal, *, limit: int = 500) -> dict[str, Any]:
    """Summarize matured lifecycle evidence without manufacturing missing data."""
    try:
        with journal.session() as con:
            rows = con.execute(
                """
                SELECT score_json FROM v2_lifecycle_forecasts
                WHERE score_json IS NOT NULL
                ORDER BY scored_at DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    except Exception:
        rows = []

    briers: list[float] = []
    duration_errors: list[float] = []
    transition_hits: list[int] = []
    for row in rows:
        try:
            score = json.loads(row["score_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        brier = _num(score.get("mean_survival_brier"))
        if brier is not None:
            briers.append(brier)
        duration = _num(score.get("duration_absolute_error"))
        if duration is not None:
            duration_errors.append(duration)
        if score.get("transition_correct") is not None:
            transition_hits.append(int(bool(score.get("transition_correct"))))

    mean_brier = sum(briers) / len(briers) if briers else None
    duration_mae = sum(duration_errors) / len(duration_errors) if duration_errors else None
    transition_accuracy = (
        sum(transition_hits) / len(transition_hits) if transition_hits else None
    )
    duration_ready = bool(
        len(briers) >= 100
        and mean_brier is not None
        and mean_brier <= 0.15
        and len(duration_errors) >= 50
        and duration_mae is not None
        and duration_mae <= 20.0
    )
    return {
        "scored_survival_forecasts": len(briers),
        "mean_survival_brier": mean_brier,
        "scored_duration_forecasts": len(duration_errors),
        "duration_mae_minutes": duration_mae,
        "scored_transitions": len(transition_hits),
        "transition_accuracy": transition_accuracy,
        "duration_calibration_ready": duration_ready,
        "transition_is_separate_authority_gate": True,
    }


def evaluate_readiness(journal) -> dict[str, Any]:
    """Return the only labels allowed for V2 research promotion.

    `soundness` is about architecture/calibration and is distinct from realized
    action value. `profitability` can become FORWARD_VALIDATED_PROFITABLE only
    through Step-16 governance on the current policy version. This function never
    enables live capital; a separate human deployment decision remains mandatory.
    """
    playbooks = evaluate_playbooks(journal)
    lifecycle = lifecycle_calibration(journal)
    validated = sorted(
        name
        for name, row in playbooks.items()
        if row.get("status") == "VALIDATED_PLAYBOOK"
        and row.get("execution_eligible") is True
        and row.get("policy_version") == CURRENT_POLICY_VERSION
        and float(row.get("forward_session_pnl_lcb95") or 0.0) > 0.0
    )
    provisional = sorted(
        name
        for name, row in playbooks.items()
        if row.get("status") == "PROVISIONAL_REPEATABLE"
        and row.get("execution_eligible") is True
        and row.get("policy_version") == CURRENT_POLICY_VERSION
        and float(row.get("forward_session_pnl_lcb90") or 0.0) > 0.0
    )

    if validated:
        profitability = "FORWARD_VALIDATED_PROFITABLE"
    elif provisional:
        profitability = "PROVISIONAL_FORWARD_EDGE"
    else:
        profitability = "UNPROVEN"

    soundness = (
        "CALIBRATED_PAPER_RESEARCH"
        if lifecycle["duration_calibration_ready"]
        else "GUARDED_PAPER_RESEARCH_COLLECTING_CALIBRATION"
    )
    return {
        "policy_version": CURRENT_POLICY_VERSION,
        "policy_contract": POLICY_CONTRACT,
        "soundness": soundness,
        "profitability": profitability,
        "validated_profitable_playbooks": validated,
        "provisional_playbooks": provisional,
        "lifecycle": lifecycle,
        "playbooks": playbooks,
        "live_capital_eligible": False,
        "live_capital_reason": "paper_forward_validation_does_not_authorize_live_capital",
    }

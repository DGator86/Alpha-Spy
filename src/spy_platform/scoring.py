from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any

from .assertions import AnalystAssertion, ManagerView


@dataclass(frozen=True)
class DirectionalScore:
    subject_id: str
    horizon_minutes: int
    realized_return_bps: float
    direction_correct: bool | None
    brier: float | None
    return_error_bps: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Scorecard:
    subject: str
    samples: int
    directional_samples: int
    direction_accuracy: float | None
    mean_brier: float | None
    mean_abs_return_error_bps: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_assertion(
    assertion: AnalystAssertion,
    *,
    realized_return_bps: float,
) -> DirectionalScore:
    horizon = int(assertion.horizon_minutes or 0)
    p_up = assertion.probability_up
    direction_correct: bool | None = None
    brier: float | None = None
    if p_up is not None and math.isfinite(float(p_up)):
        probability = _clamp_probability(float(p_up))
        outcome = 1.0 if realized_return_bps > 0 else 0.0
        brier = (probability - outcome) ** 2
        predicted_up = probability >= 0.5
        actual_up = realized_return_bps > 0
        direction_correct = predicted_up == actual_up
    elif assertion.directional_bias in {"BULLISH", "BEARISH"}:
        predicted_up = assertion.directional_bias == "BULLISH"
        actual_up = realized_return_bps > 0
        direction_correct = predicted_up == actual_up

    return_error = None
    if assertion.expected_return_bps is not None:
        return_error = abs(float(assertion.expected_return_bps) - float(realized_return_bps))

    return DirectionalScore(
        subject_id=assertion.assertion_id,
        horizon_minutes=horizon,
        realized_return_bps=float(realized_return_bps),
        direction_correct=direction_correct,
        brier=brier,
        return_error_bps=return_error,
    )


def score_manager_view(
    view: ManagerView,
    *,
    horizon_minutes: int,
    realized_return_bps: float,
) -> DirectionalScore:
    probability = view.probability_up
    direction_correct = None
    brier = None
    if probability is not None and math.isfinite(float(probability)):
        p_up = _clamp_probability(float(probability))
        outcome = 1.0 if realized_return_bps > 0 else 0.0
        brier = (p_up - outcome) ** 2
        direction_correct = (p_up >= 0.5) == (realized_return_bps > 0)
    elif str(view.bias).upper() in {"BULLISH", "BEARISH"}:
        direction_correct = (str(view.bias).upper() == "BULLISH") == (realized_return_bps > 0)
    return_error = (
        abs(float(view.expected_return_bps) - float(realized_return_bps))
        if view.expected_return_bps is not None
        else None
    )
    return DirectionalScore(
        subject_id=view.view_id,
        horizon_minutes=int(horizon_minutes),
        realized_return_bps=float(realized_return_bps),
        direction_correct=direction_correct,
        brier=brier,
        return_error_bps=return_error,
    )


def aggregate_scorecard(subject: str, scores: list[DirectionalScore]) -> Scorecard:
    directional = [score for score in scores if score.direction_correct is not None]
    briers = [score.brier for score in scores if score.brier is not None]
    errors = [score.return_error_bps for score in scores if score.return_error_bps is not None]
    return Scorecard(
        subject=subject,
        samples=len(scores),
        directional_samples=len(directional),
        direction_accuracy=(
            mean(1.0 if score.direction_correct else 0.0 for score in directional)
            if directional
            else None
        ),
        mean_brier=mean(briers) if briers else None,
        mean_abs_return_error_bps=mean(errors) if errors else None,
    )

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contracts import AlphaState, BetaState


@dataclass(frozen=True)
class EquivalenceResult:
    model: str
    equivalent: bool
    compared_fields: int
    mismatches: tuple[str, ...]
    tolerance: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compare_numeric_maps(
    name: str,
    baseline: dict[int, float],
    candidate: dict[int, float],
    *,
    tolerance: float,
) -> list[str]:
    mismatches: list[str] = []
    keys = sorted(set(baseline) | set(candidate))
    for key in keys:
        if key not in baseline:
            mismatches.append(f"{name}[{key}]:candidate_only")
            continue
        if key not in candidate:
            mismatches.append(f"{name}[{key}]:baseline_only")
            continue
        if abs(float(baseline[key]) - float(candidate[key])) > tolerance:
            mismatches.append(
                f"{name}[{key}]:{baseline[key]:.12g}!={candidate[key]:.12g}"
            )
    return mismatches


def compare_alpha_states(
    baseline: AlphaState,
    candidate: AlphaState,
    *,
    tolerance: float = 1e-12,
) -> EquivalenceResult:
    mismatches = [
        *_compare_numeric_maps(
            "probability_up",
            baseline.probability_up,
            candidate.probability_up,
            tolerance=tolerance,
        ),
        *_compare_numeric_maps(
            "expected_return_bps",
            baseline.expected_return_bps,
            candidate.expected_return_bps,
            tolerance=tolerance,
        ),
    ]
    if baseline.regime != candidate.regime:
        mismatches.append("regime")
    if baseline.lifecycle != candidate.lifecycle:
        mismatches.append("lifecycle")
    return EquivalenceResult(
        model="ALPHA",
        equivalent=not mismatches,
        compared_fields=(
            len(set(baseline.probability_up) | set(candidate.probability_up))
            + len(set(baseline.expected_return_bps) | set(candidate.expected_return_bps))
            + 2
        ),
        mismatches=tuple(mismatches),
        tolerance=tolerance,
    )


def compare_beta_states(
    baseline: BetaState,
    candidate: BetaState,
    *,
    tolerance: float = 1e-12,
) -> EquivalenceResult:
    mismatches = [
        *_compare_numeric_maps(
            "probability_up",
            baseline.probability_up,
            candidate.probability_up,
            tolerance=tolerance,
        ),
        *_compare_numeric_maps(
            "expected_return_bps",
            baseline.expected_return_bps,
            candidate.expected_return_bps,
            tolerance=tolerance,
        ),
    ]
    for field in ("breadth", "sectors", "flow", "participation", "microstructure"):
        if getattr(baseline, field) != getattr(candidate, field):
            mismatches.append(field)
    return EquivalenceResult(
        model="BETA",
        equivalent=not mismatches,
        compared_fields=(
            len(set(baseline.probability_up) | set(candidate.probability_up))
            + len(set(baseline.expected_return_bps) | set(candidate.expected_return_bps))
            + 5
        ),
        mismatches=tuple(mismatches),
        tolerance=tolerance,
    )

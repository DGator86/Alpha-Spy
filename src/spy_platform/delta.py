from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

from .contracts import AlphaState, BetaState, DeltaState, GammaState


def _parse(value: str) -> datetime:
    out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if out.tzinfo is None:
        out = out.replace(tzinfo=UTC)
    return out.astimezone(UTC)


def _score(probability_up: float | None) -> float | None:
    if probability_up is None:
        return None
    value = float(probability_up)
    if not math.isfinite(value):
        return None
    return max(-1.0, min(1.0, 2.0 * value - 1.0))


def _pairwise_abs(values: list[float]) -> float:
    pairs = [abs(a - b) for a, b in combinations(values, 2)]
    return float(statistics.mean(pairs)) if pairs else 0.0


def _horizon_convergence(
    alpha: AlphaState,
    beta: BetaState,
    gamma: GammaState,
) -> dict[int, dict[str, Any]]:
    horizons = sorted(set(alpha.probability_up) | set(beta.probability_up))
    out: dict[int, dict[str, Any]] = {}
    for horizon in horizons:
        scores: dict[str, float] = {}
        alpha_score = _score(alpha.probability_up.get(horizon))
        beta_score = _score(beta.probability_up.get(horizon))
        if alpha_score is not None:
            scores["alpha"] = alpha_score
        if beta_score is not None:
            scores["beta"] = beta_score
        if gamma.directional_score is not None:
            scores["gamma"] = max(-1.0, min(1.0, float(gamma.directional_score)))

        values = list(scores.values())
        mean_score = float(statistics.mean(values)) if values else None
        mean_abs = float(statistics.mean(abs(value) for value in values)) if values else None
        agreement = (
            abs(mean_score) / mean_abs
            if mean_score is not None and mean_abs is not None and mean_abs > 1e-12
            else 1.0 if values and all(abs(value) <= 1e-12 for value in values)
            else None
        )
        divergence = float(statistics.pstdev(values)) if len(values) >= 2 else 0.0 if values else None
        out[horizon] = {
            "scores": scores,
            "mean_direction_score": mean_score,
            "directional_agreement": agreement,
            "directional_divergence": divergence,
            "mean_pairwise_distance": _pairwise_abs(values) if values else None,
            "model_count": len(values),
        }
    return out


def compile_delta_state(
    alpha: AlphaState,
    beta: BetaState,
    gamma: GammaState,
    *,
    previous: DeltaState | None = None,
) -> DeltaState:
    """Compile independent model outputs without generating a trade thesis."""
    stamps = [_parse(alpha.meta.timestamp), _parse(beta.meta.timestamp), _parse(gamma.meta.timestamp)]
    timestamp = max(stamps)
    horizon = _horizon_convergence(alpha, beta, gamma)

    conflicts: list[str] = []
    for minutes, state in horizon.items():
        scores = state["scores"]
        signs = {name: 1 if value > 0 else -1 if value < 0 else 0 for name, value in scores.items()}
        nonzero = {value for value in signs.values() if value}
        if len(nonzero) > 1:
            conflicts.append(f"DIRECTION_SIGN_CONFLICT_{minutes}M")
        if (state["directional_divergence"] or 0.0) >= 0.35:
            conflicts.append(f"HIGH_DIRECTION_DIVERGENCE_{minutes}M")

    qualities = {
        "alpha": alpha.meta.data_quality,
        "beta": beta.meta.data_quality,
        "gamma": gamma.meta.data_quality,
    }
    ages = {
        "alpha": alpha.meta.source_age_seconds,
        "beta": beta.meta.source_age_seconds,
        "gamma": gamma.meta.source_age_seconds,
    }
    anomalies: list[str] = []
    for model, quality in qualities.items():
        if quality < 0.70:
            anomalies.append(f"LOW_DATA_QUALITY_{model.upper()}")
    for model, age in ages.items():
        if age > 120:
            anomalies.append(f"STALE_SOURCE_{model.upper()}")
    if gamma.metrics.get("chain_count", 0) == 0:
        anomalies.append("GAMMA_NO_OPTION_CHAINS")

    model_changes: dict[str, Any] = {}
    if previous is not None:
        previous_horizons = previous.convergence.get("horizons", {})
        for minutes, state in horizon.items():
            old = previous_horizons.get(minutes) or previous_horizons.get(str(minutes)) or {}
            old_mean = old.get("mean_direction_score")
            new_mean = state.get("mean_direction_score")
            if old_mean is not None and new_mean is not None:
                model_changes[f"direction_{minutes}m"] = float(new_mean) - float(old_mean)

    convergence = {
        "horizons": horizon,
        "model_versions": {
            "alpha": alpha.meta.model_version,
            "beta": beta.meta.model_version,
            "gamma": gamma.meta.model_version,
        },
    }
    data_quality = {
        "models": qualities,
        "source_age_seconds": ages,
        "composite": float(statistics.mean(qualities.values())),
        "max_clock_skew_seconds": max((timestamp - stamp).total_seconds() for stamp in stamps),
    }

    return DeltaState(
        timestamp=timestamp.isoformat().replace("+00:00", "Z"),
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        convergence=convergence,
        conflicts=tuple(sorted(set(conflicts))),
        anomalies=tuple(sorted(set(anomalies))),
        data_quality=data_quality,
        model_changes=model_changes,
    )

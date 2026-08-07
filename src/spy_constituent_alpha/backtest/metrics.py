from __future__ import annotations

import numpy as np


def brier_score(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    return float(np.mean((p - y) ** 2))


def log_score(probabilities: np.ndarray, outcomes: np.ndarray, epsilon: float = 1e-9) -> float:
    p = np.clip(np.asarray(probabilities, dtype=float), epsilon, 1.0 - epsilon)
    y = np.asarray(outcomes, dtype=float)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def calibration_error(probabilities: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> float:
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    error = 0.0
    for i in range(bins):
        mask = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if mask.any():
            error += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return float(error)

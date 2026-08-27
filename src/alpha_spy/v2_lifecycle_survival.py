from __future__ import annotations

import math
import uuid
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression

from .v2_lifecycle import (
    REGIMES,
    AlphaRegimeLifecycleEngine,
    LifecycleForecast,
    _clip,
    _effective_sample_size,
    _iso,
    _num,
    _weighted_quantile,
)

_HORIZONS = (5, 15, 30)
_MAX_RISK_SET_AGE = 45
_MIN_COMPLETED_EPISODES = 5
_MIN_RISK_ROWS = 30


def _risk_features(row: dict[str, Any], age_minutes: float) -> np.ndarray:
    regime = str(row.get("canonical_regime") or "TRANSITION")
    one_hot = [1.0 if regime == name else 0.0 for name in REGIMES]
    age = max(0.0, min(float(age_minutes), 60.0)) / 30.0
    interactions = [value * age for value in one_hot]
    numeric = [
        age,
        age * age,
        _clip(row.get("conflict_score"), 0.0, 1.5) / 1.5,
        float(bool(row.get("transition_risk"))),
        _clip(row.get("beta_strength"), 0.0, 2.0) / 2.0,
        _clip(row.get("beta_p_big_15")),
        _clip(row.get("beta_p_big_30")),
        _clip(row.get("beta_p_reversal_15")),
        _clip(row.get("beta_p_persistent_30")),
    ]
    return np.asarray([*one_hot, *interactions, *numeric], dtype=float)


def _fit_survival_models(
    episodes: list[dict[str, Any]],
) -> tuple[dict[int, LogisticRegression], int, int]:
    vectors: list[np.ndarray] = []
    labels: dict[int, list[int]] = {horizon: [] for horizon in _HORIZONS}
    weights: list[float] = []
    completed = [episode for episode in episodes if episode.get("completed")]

    for episode in completed:
        duration = _num(episode.get("duration_minutes"))
        if duration <= 0.0:
            continue
        ages = list(range(0, int(min(duration, _MAX_RISK_SET_AGE)) + 1, 5)) or [0]
        episode_weight = 1.0 / max(len(ages), 1)
        start_row = episode["start_row"]
        for age in ages:
            remaining = max(0.0, duration - float(age))
            vectors.append(_risk_features(start_row, float(age)))
            weights.append(episode_weight)
            for horizon in _HORIZONS:
                labels[horizon].append(int(remaining >= float(horizon)))

    if not vectors:
        return {}, 0, len(completed)
    x = np.vstack(vectors)
    sample_weight = np.asarray(weights, dtype=float)
    models: dict[int, LogisticRegression] = {}
    for horizon in _HORIZONS:
        y = np.asarray(labels[horizon], dtype=int)
        if y.size < _MIN_RISK_ROWS or len(np.unique(y)) < 2:
            continue
        model = LogisticRegression(
            C=0.45,
            max_iter=400,
            solver="lbfgs",
        )
        model.fit(x, y, sample_weight=sample_weight)
        models[horizon] = model
    return models, len(vectors), len(completed)


def _survival_probability(
    model: LogisticRegression | None,
    current_row: dict[str, Any],
    age: float,
    horizon: int,
    episodes: list[dict[str, Any]],
) -> float:
    if model is not None:
        vector = _risk_features(current_row, age).reshape(1, -1)
        return _clip(float(model.predict_proba(vector)[0, 1]))

    # Honest empirical fallback when one horizon has not yet seen both outcomes.
    at_risk = [
        episode
        for episode in episodes
        if episode.get("completed") and _num(episode.get("duration_minutes")) >= age
    ]
    if not at_risk:
        return 0.5
    successes = sum(
        _num(episode.get("duration_minutes")) - age >= horizon for episode in at_risk
    )
    # Beta(1,1) smoothing avoids exact zero/one during cold start.
    return float((successes + 1.0) / (len(at_risk) + 2.0))


def _weighted_remaining(
    current_row: dict[str, Any],
    historical: list[dict[str, Any]],
    age: float,
) -> tuple[list[float], list[float]]:
    at_risk = [
        episode
        for episode in historical
        if _num(episode.get("duration_minutes")) >= age
    ]
    if not at_risk:
        return [], []
    weights = [
        AlphaRegimeLifecycleEngine._similarity(current_row, episode)
        for episode in at_risk
    ]
    remaining = [
        max(0.0, _num(episode.get("duration_minutes")) - age)
        for episode in at_risk
    ]
    return remaining, weights


class AlphaRiskSetLifecycleEngine(AlphaRegimeLifecycleEngine):
    """Blocked/risk-set survival lifecycle downstream of Alpha regime authority.

    The original V2 lifecycle waited for ten completed *same-regime* episodes,
    which made Steps 3-4 unusably sparse. This model pools completed prior Alpha
    episodes in a regularized discrete-time risk set while preserving the current
    Alpha regime as a categorical covariate. The current episode remains censored
    and is never used as a completed target.

    Beta is only a covariate. It cannot define Alpha's regime. Successor direction
    remains advisory until its own scored calibration earns authority.
    """

    def forecast(
        self,
        *,
        snapshot_id: str,
        captured_at: str,
        hierarchy,
        beta: dict[str, Any] | None,
    ) -> LifecycleForecast:
        stamp = _iso(captured_at)
        self.score_matured_forecasts(now=stamp)
        observation = self.record_observation(
            snapshot_id=snapshot_id,
            captured_at=captured_at,
            hierarchy=hierarchy,
            beta=beta,
        )
        regime = observation["canonical_regime"]
        witness = observation["beta_witness"]
        rows = self._rows()
        episodes = self._episodes(rows)
        current_episode = episodes[-1] if episodes else None
        age = (
            max(0.0, (stamp - current_episode["started_at"]).total_seconds() / 60.0)
            if current_episode is not None
            else 0.0
        )
        current_row = rows[-1] if rows else {}
        historical = [
            episode
            for episode in episodes[:-1]
            if episode.get("completed")
        ]

        models, risk_rows, completed_count = _fit_survival_models(historical)
        p5 = _survival_probability(models.get(5), current_row, age, 5, historical)
        p15 = _survival_probability(models.get(15), current_row, age, 15, historical)
        p30 = _survival_probability(models.get(30), current_row, age, 30, historical)
        # Survival must be monotone in horizon even when independent logistic
        # estimators have small-sample calibration noise.
        p5 = _clip(p5)
        p15 = min(p5, _clip(p15))
        p30 = min(p15, _clip(p30))

        same_regime = [
            episode
            for episode in historical
            if episode.get("regime") == regime
            and _num(episode.get("duration_minutes")) >= age
        ]
        remaining, weights = _weighted_remaining(current_row, same_regime, age)
        if len(remaining) < 3:
            remaining, weights = _weighted_remaining(current_row, historical, age)

        if remaining and sum(weights) > 0.0:
            total = sum(weights)
            expected = sum(value * weight for value, weight in zip(remaining, weights, strict=False)) / total
            expected = float(np.clip(expected, 3.0, 60.0))
            quantiles = {
                "p10": _weighted_quantile(remaining, weights, 0.10),
                "p25": _weighted_quantile(remaining, weights, 0.25),
                "p50": _weighted_quantile(remaining, weights, 0.50),
                "p75": _weighted_quantile(remaining, weights, 0.75),
                "p90": _weighted_quantile(remaining, weights, 0.90),
            }
        else:
            expected = max(3.0, min(45.0, 5.0 + 10.0 * p15 + 20.0 * p30))
            quantiles = {
                "p10": 0.35 * expected,
                "p25": 0.60 * expected,
                "p50": expected,
                "p75": 1.30 * expected,
                "p90": 1.60 * expected,
            }

        # Step 4 is deliberately separate from Step 3. Estimate the successor
        # distribution from prior same-regime episodes with Dirichlet shrinkage,
        # but do not grant it hard-veto authority here.
        successor_pool = [
            episode
            for episode in historical
            if episode.get("regime") == regime
            and episode.get("successor") in REGIMES
            and episode.get("successor") != regime
            and _num(episode.get("duration_minutes")) >= age
        ]
        successor_weights = [
            self._similarity(current_row, episode) for episode in successor_pool
        ]
        successor_raw = {
            name: (0.0 if name == regime else 0.50)
            for name in REGIMES
        }
        for episode, weight in zip(successor_pool, successor_weights, strict=False):
            successor_raw[str(episode["successor"])] += weight
        successor_total = sum(successor_raw.values()) or 1.0
        successors = {
            name: value / successor_total for name, value in successor_raw.items()
        }
        successor_candidates = {
            name: probability
            for name, probability in successors.items()
            if name != regime
        }
        successor = max(
            successor_candidates,
            key=successor_candidates.get,
            default="TRANSITION",
        )
        successor_conf = float(successor_candidates.get(successor, 0.0))

        calibration = self._calibration()
        scored = int(calibration.get("scored_forecasts") or 0)
        transition_accuracy = calibration.get("transition_accuracy")
        successor_authority = bool(
            scored >= 100
            and transition_accuracy is not None
            and float(transition_accuracy) >= 0.60
        )
        calibration = {
            **calibration,
            "risk_set_rows": risk_rows,
            "completed_prior_episodes": completed_count,
            "fitted_horizons": sorted(models),
            "successor_authority": successor_authority,
            "successor_authority_threshold": {
                "minimum_scored_forecasts": 100,
                "minimum_transition_accuracy": 0.60,
            },
        }

        brier = calibration.get("mean_survival_brier")
        calibration_factor = (
            1.0
            if brier is None
            else max(0.35, 1.0 - min(1.0, float(brier)))
        )
        alpha_support = min(
            1.0,
            max(0.0, 1.0 - float(hierarchy.conflict_score) / 1.25),
        )
        episode_support = min(1.0, completed_count / 15.0)
        row_support = min(1.0, risk_rows / 100.0)
        model_support = 0.5 * episode_support + 0.5 * row_support
        confidence = _clip(
            0.45 * alpha_support
            + 0.40 * model_support
            + 0.15 * calibration_factor
        )
        all_horizons_fit = all(horizon in models for horizon in _HORIZONS)
        definable = bool(
            completed_count >= _MIN_COMPLETED_EPISODES
            and risk_rows >= _MIN_RISK_ROWS
            and all_horizons_fit
            and confidence >= 0.40
        )

        hazard0_5 = 1.0 - p5
        hazard5_15 = 1.0 - (p15 / p5 if p5 > 1e-9 else 0.0)
        hazard15_30 = 1.0 - (p30 / p15 if p15 > 1e-9 else 0.0)
        effective = _effective_sample_size(weights) if weights else 0.0
        reasons = [
            "alpha_hierarchy_is_regime_authority",
            "completed_prior_alpha_episodes_only",
            "blocked_discrete_time_risk_set_survival",
            "beta_is_covariate_not_regime_authority",
            "successor_direction_advisory_until_calibrated",
        ]
        if not definable:
            reasons.append("risk_set_lifecycle_support_not_yet_sufficient")

        forecast = LifecycleForecast(
            forecast_id=f"LC-{uuid.uuid4().hex[:16]}",
            created_at=stamp.isoformat(),
            source="DISCRETE_TIME_ALPHA_RISK_SET_SURVIVAL",
            current_regime=regime,
            alpha_hierarchy=hierarchy.as_dict(),
            regime_age_minutes=age,
            definable=definable,
            confidence=confidence,
            persistence_5=p5,
            persistence_15=p15,
            persistence_30=p30,
            hazard_0_5=_clip(hazard0_5),
            hazard_5_15=_clip(hazard5_15),
            hazard_15_30=_clip(hazard15_30),
            expected_remaining_minutes=expected,
            remaining_duration_quantiles=quantiles,
            successor_probabilities=successors,
            most_likely_successor=successor,
            successor_confidence=successor_conf,
            matched_episodes=len(same_regime),
            effective_episodes=effective,
            calibration=calibration,
            beta_witness=witness,
            reasons=tuple(reasons),
        )
        with self.journal.transaction() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO v2_lifecycle_forecasts(
                    forecast_id,created_at,snapshot_id,current_regime,regime_age_minutes,
                    p_survive_5,p_survive_15,p_survive_30,expected_remaining_minutes,
                    successor_probabilities_json,source,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    forecast.forecast_id,
                    forecast.created_at,
                    snapshot_id,
                    forecast.current_regime,
                    forecast.regime_age_minutes,
                    forecast.persistence_5,
                    forecast.persistence_15,
                    forecast.persistence_30,
                    forecast.expected_remaining_minutes,
                    self.journal._json(forecast.successor_probabilities),
                    forecast.source,
                    self.journal._json(forecast.as_dict()),
                ),
            )
        return forecast

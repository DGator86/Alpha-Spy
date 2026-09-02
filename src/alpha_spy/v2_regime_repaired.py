from __future__ import annotations

from datetime import datetime, time
from typing import Any

import numpy as np

from .context import MarketContext
from .db import Journal
from .regime import _context_labels, RegimeHierarchy, RegimeState
from .timeutil import ET


_CONFLICT_WEIGHTS = {
    "volatility": 0.35,
    "correlation": 0.25,
    "breadth": 0.30,
    "concentration": 0.10,
}

_VOLATILITY_ORDER = {"low": 0.0, "normal": 1.0 / 3.0, "high": 2.0 / 3.0, "crisis": 1.0}
_CORRELATION_ORDER = {
    "falling": 0.0,
    "stable": 1.0 / 3.0,
    "rising": 2.0 / 3.0,
    "dislocated": 1.0,
    "unknown_correlation": 0.5,
}
_BREADTH_ORDER = {"broad_down": 0.0, "mixed": 0.5, "broad_up": 1.0}
_CONCENTRATION_ORDER = {"distributed": 0.0, "concentrated": 1.0}


def tie_aware_percentile(history: np.ndarray, current: float) -> float:
    """Empirical mid-rank percentile that cannot turn a tied floor into 100%."""
    values = np.asarray(history, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.5
    below = float(np.mean(values < current))
    tied = float(np.mean(values == current))
    return float(np.clip(below + 0.5 * tied, 0.0, 1.0))


def _horizon_ewma(current: float, history: np.ndarray, lookback: int) -> float:
    """Causal level state with a half-life tied to the hierarchy horizon.

    ``history`` is newest-first and excludes the current observation. The old
    hierarchy used the same current breadth/concentration at every level, which
    made those dimensions incapable of expressing cross-horizon disagreement.
    This smoother gives each level a genuinely different memory while keeping the
    current observation highest-weighted.
    """
    values = np.asarray(history, dtype=float)
    values = values[np.isfinite(values)]
    values = values[: max(1, int(lookback))]
    series = np.concatenate(([float(current)], values))
    half_life = max(5.0, float(lookback) / 4.0)
    ages = np.arange(series.size, dtype=float)
    weights = np.exp(-np.log(2.0) * ages / half_life)
    return float(np.dot(series, weights) / np.sum(weights))


def _range_disagreement(
    states: list[RegimeState],
    field: str,
    mapping: dict[str, float],
) -> float:
    values = [mapping.get(str(getattr(state, field)), 0.5) for state in states]
    if not values:
        return 0.0
    return float(max(values) - min(values))


def hierarchy_conflict_score(levels: dict[str, RegimeState]) -> float:
    """Weighted cross-horizon disagreement on fields that can vary by horizon.

    The production V1 score used breadth and risk tone, but both were identical at
    every level, making the score identically zero. V2 measures disagreement in
    four level-specific dimensions. The result remains on [0, 1], preserving the
    existing 0.65 transition threshold and downstream confidence scaling.
    """
    states = list(levels.values())
    score = (
        _CONFLICT_WEIGHTS["volatility"]
        * _range_disagreement(states, "volatility", _VOLATILITY_ORDER)
        + _CONFLICT_WEIGHTS["correlation"]
        * _range_disagreement(states, "correlation", _CORRELATION_ORDER)
        + _CONFLICT_WEIGHTS["breadth"]
        * _range_disagreement(states, "breadth", _BREADTH_ORDER)
        + _CONFLICT_WEIGHTS["concentration"]
        * _range_disagreement(states, "concentration", _CONCENTRATION_ORDER)
    )
    return float(np.clip(score, 0.0, 1.0))


def classify_regime(
    journal: Journal,
    *,
    timestamp: datetime,
    feature: dict[str, Any],
    gamma_state: str,
    event_state: str,
    lookback: int = 240,
    context: MarketContext | None = None,
) -> RegimeState:
    """Causal Alpha regime classifier with level-specific state and tie-safe ranks."""
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT realized_vol, correlation, breadth, concentration
            FROM features
            WHERE created_at < ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (timestamp.isoformat().replace("+00:00", "Z"), max(20, int(lookback))),
        ).fetchall()

    vol_hist = np.asarray(
        [float(row["realized_vol"]) for row in rows if row["realized_vol"] is not None],
        dtype=float,
    )
    corr_hist = np.asarray(
        [float(row["correlation"]) for row in rows if row["correlation"] is not None],
        dtype=float,
    )
    breadth_hist = np.asarray(
        [float(row["breadth"]) for row in rows if row["breadth"] is not None],
        dtype=float,
    )
    concentration_hist = np.asarray(
        [
            float(row["concentration"])
            for row in rows
            if row["concentration"] is not None
        ],
        dtype=float,
    )

    realized_vol = float(feature.get("realized_vol") or 0.0)
    level_vol = _horizon_ewma(realized_vol, vol_hist, lookback)
    if vol_hist.size >= 20:
        vol_q = tie_aware_percentile(vol_hist, level_vol)
        volatility = (
            "low"
            if vol_q < 0.25
            else "normal"
            if vol_q < 0.75
            else "high"
            if vol_q < 0.95
            else "crisis"
        )
    else:
        volatility = "normal"

    current_corr = feature.get("correlation")
    if current_corr is None or not np.isfinite(float(current_corr)):
        correlation = "unknown_correlation"
    else:
        level_corr = _horizon_ewma(float(current_corr), corr_hist, lookback)
        corr_q = (
            tie_aware_percentile(corr_hist, level_corr)
            if corr_hist.size >= 20
            else 0.5
        )
        if corr_hist.size >= 20:
            recent_n = min(max(5, lookback // 20), len(corr_hist))
            prior_n = min(max(recent_n + 5, lookback // 5), len(corr_hist))
            recent = float(np.mean(corr_hist[:recent_n]))
            prior = (
                float(np.mean(corr_hist[recent_n:prior_n]))
                if prior_n > recent_n
                else recent
            )
            delta = recent - prior
        else:
            delta = 0.0
        correlation = (
            "dislocated"
            if corr_q > 0.95 or level_corr >= 0.90
            else "rising"
            if delta > 0.03
            else "falling"
            if delta < -0.03
            else "stable"
        )

    breadth_current = float(feature.get("breadth") or 0.5)
    breadth_value = _horizon_ewma(breadth_current, breadth_hist, lookback)
    breadth = (
        "broad_up"
        if breadth_value >= 0.70
        else "broad_down"
        if breadth_value <= 0.30
        else "mixed"
    )

    concentration_current = float(feature.get("concentration") or 0.0)
    concentration_value = _horizon_ewma(
        concentration_current,
        concentration_hist,
        lookback,
    )
    concentration = "concentrated" if concentration_value >= 0.18 else "distributed"

    local = timestamp.astimezone(ET).timetz().replace(tzinfo=None)
    session = (
        "opening"
        if local < time(10, 0)
        else "midday"
        if local < time(15, 0)
        else "final_hour"
        if local < time(15, 50)
        else "expiration_window"
    )

    allowed_events = {
        "ordinary",
        "earnings_heavy",
        "macro_announcement",
        "rebalance",
        "unknown",
    }
    event = event_state if event_state in allowed_events else "unknown"
    risk_tone, volatility_term, liquidity = _context_labels(context)
    transition_risk = bool(
        volatility in {"high", "crisis"}
        or correlation in {"rising", "dislocated", "unknown_correlation"}
        or gamma_state in {"negative_gamma", "unknown_gamma"}
        or event in {"macro_announcement", "rebalance", "unknown"}
        or volatility_term == "backwardation"
        or liquidity == "thin"
    )
    return RegimeState(
        volatility=volatility,
        correlation=correlation,
        breadth=breadth,
        concentration=concentration,
        dealer_gamma=gamma_state,
        session=session,
        event=event,
        transition_risk=transition_risk,
        history_samples=len(rows),
        risk_tone=risk_tone,
        volatility_term=volatility_term,
        liquidity=liquidity,
    )


def classify_regime_hierarchy(
    journal: Journal,
    *,
    timestamp: datetime,
    feature: dict[str, Any],
    gamma_state: str,
    event_state: str,
    context: MarketContext | None = None,
) -> RegimeHierarchy:
    levels = {
        "micro": classify_regime(
            journal,
            timestamp=timestamp,
            feature=feature,
            gamma_state=gamma_state,
            event_state=event_state,
            lookback=45,
            context=context,
        ),
        "intraday": classify_regime(
            journal,
            timestamp=timestamp,
            feature=feature,
            gamma_state=gamma_state,
            event_state=event_state,
            lookback=240,
            context=context,
        ),
        "swing": classify_regime(
            journal,
            timestamp=timestamp,
            feature=feature,
            gamma_state=gamma_state,
            event_state=event_state,
            lookback=780,
            context=context,
        ),
        "structural": classify_regime(
            journal,
            timestamp=timestamp,
            feature=feature,
            gamma_state=gamma_state,
            event_state=event_state,
            lookback=1950,
            context=context,
        ),
    }
    conflict_score = hierarchy_conflict_score(levels)
    transition = conflict_score >= 0.65 or any(
        state.transition_risk for state in levels.values()
    )
    return RegimeHierarchy(
        micro=levels["micro"],
        intraday=levels["intraday"],
        swing=levels["swing"],
        structural=levels["structural"],
        conflict_score=conflict_score,
        transition_risk=transition,
    )

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import numpy as np

from .context import MarketContext
from .db import Journal
from .regime import RegimeHierarchy, RegimeState, _context_labels
from .timeutil import ET


def tie_aware_percentile(history: np.ndarray, current: float) -> float:
    """Empirical mid-rank percentile that cannot turn a tied floor into 100%.

    Alpha clips realized volatility to a configured floor. A conventional
    ``mean(history <= current)`` assigns every tied floor observation the maximum
    empirical rank and can therefore label the quietest possible observation as
    crisis volatility. Mid-rank ECDF treatment assigns half of the tied mass below
    and half above the observation, which is the correct rank treatment here.
    """
    values = np.asarray(history, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.5
    below = float(np.mean(values < current))
    tied = float(np.mean(values == current))
    return float(np.clip(below + 0.5 * tied, 0.0, 1.0))


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
    """Causal Alpha regime classifier with tie-safe volatility ranks."""
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
    realized_vol = float(feature.get("realized_vol") or 0.0)
    current_corr = feature.get("correlation")

    if vol_hist.size >= 20:
        vol_q = tie_aware_percentile(vol_hist, realized_vol)
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

    if current_corr is None or not np.isfinite(float(current_corr)):
        correlation = "unknown_correlation"
    else:
        current = float(current_corr)
        corr_q = tie_aware_percentile(corr_hist, current) if corr_hist.size >= 20 else 0.5
        if corr_hist.size >= 20:
            recent_n = min(5, len(corr_hist))
            prior_n = min(20, len(corr_hist))
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
            if corr_q > 0.95 or current >= 0.90
            else "rising"
            if delta > 0.03
            else "falling"
            if delta < -0.03
            else "stable"
        )

    breadth_value = float(feature.get("breadth") or 0.5)
    breadth = (
        "broad_up"
        if breadth_value >= 0.70
        else "broad_down"
        if breadth_value <= 0.30
        else "mixed"
    )
    concentration_value = float(feature.get("concentration") or 0.0)
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
    signs: list[float] = []
    for state in levels.values():
        if state.breadth == "broad_up" or state.risk_tone == "risk_on":
            signs.append(1.0)
        elif state.breadth == "broad_down" or state.risk_tone == "risk_off":
            signs.append(-1.0)
        else:
            signs.append(0.0)
    conflict_score = float(np.std(signs))
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

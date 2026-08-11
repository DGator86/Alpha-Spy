from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any

import numpy as np

from .context import MarketContext
from .db import Journal
from .timeutil import ET


@dataclass(frozen=True)
class RegimeState:
    volatility: str
    correlation: str
    breadth: str
    concentration: str
    dealer_gamma: str
    session: str
    event: str
    transition_risk: bool
    history_samples: int
    risk_tone: str = "neutral"
    volatility_term: str = "unknown"
    liquidity: str = "normal"

    @property
    def key(self) -> str:
        return "|".join(
            [
                self.volatility,
                self.correlation,
                self.breadth,
                self.concentration,
                self.dealer_gamma,
                self.session,
                self.event,
                self.risk_tone,
                self.volatility_term,
                self.liquidity,
            ]
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegimeHierarchy:
    micro: RegimeState
    intraday: RegimeState
    swing: RegimeState
    structural: RegimeState
    conflict_score: float
    transition_risk: bool

    @property
    def key(self) -> str:
        return f"micro={self.micro.key}||intraday={self.intraday.key}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "micro": self.micro.as_dict(),
            "intraday": self.intraday.as_dict(),
            "swing": self.swing.as_dict(),
            "structural": self.structural.as_dict(),
            "conflict_score": self.conflict_score,
            "transition_risk": self.transition_risk,
        }


def estimate_dealer_gamma_proxy(options: list[dict[str, Any]], spot: float) -> dict[str, Any]:
    """Transparent SPY gamma-exposure proxy, never a claimed dealer inventory book."""
    rows = []
    for row in options:
        gamma = row.get("gamma")
        oi = row.get("open_interest")
        if gamma is None or oi is None:
            continue
        try:
            gamma_f = float(gamma)
            oi_f = float(oi)
            if not np.isfinite(gamma_f) or not np.isfinite(oi_f) or oi_f <= 0:
                continue
        except (TypeError, ValueError):
            continue
        sign = 1.0 if str(row.get("right") or "").upper().startswith("C") else -1.0
        rows.append(sign * gamma_f * oi_f * 100.0 * spot * spot * 0.01)

    gross = float(sum(abs(x) for x in rows))
    net = float(sum(rows))
    if len(rows) < 10:
        return {
            "state": "unknown_gamma",
            "normalized": None,
            "gross_abs": gross,
            "net": net,
            "contracts": len(rows),
            "source": "spy_option_chain_proxy",
        }
    normalized = net / max(gross, 1e-12)
    state = "positive_gamma" if normalized >= 0.10 else "negative_gamma" if normalized <= -0.10 else "neutral_gamma"
    return {
        "state": state,
        "normalized": normalized,
        "gross_abs": gross,
        "net": net,
        "contracts": len(rows),
        "source": "spy_option_chain_proxy",
    }



def estimate_option_activity_proxy(options: list[dict[str, Any]], spot: float) -> dict[str, Any]:
    """SPY option activity proxy from the observable chain.

    Chain volume/open interest are activity, not aggressor-side order flow.  The
    output therefore never claims sweep, cancellation or complex-order detection.
    """
    call_volume = put_volume = call_oi = put_oi = 0.0
    signed_delta_volume = gross_delta_volume = 0.0
    near_atm_volume = total_volume = 0.0
    rows = 0
    for row in options:
        try:
            volume = max(float(row.get("volume") or 0.0), 0.0)
            oi = max(float(row.get("open_interest") or 0.0), 0.0)
            strike = float(row.get("strike") or 0.0)
        except (TypeError, ValueError):
            continue
        right = str(row.get("right") or "").upper()[:1]
        if right not in {"C", "P"}:
            continue
        rows += 1
        total_volume += volume
        if right == "C":
            call_volume += volume
            call_oi += oi
        else:
            put_volume += volume
            put_oi += oi
        delta = row.get("delta")
        try:
            delta_f = float(delta) if delta is not None else None
        except (TypeError, ValueError):
            delta_f = None
        if delta_f is not None and np.isfinite(delta_f):
            signed_delta_volume += delta_f * volume
            gross_delta_volume += abs(delta_f) * volume
        if spot > 0 and abs(strike / spot - 1.0) <= 0.01:
            near_atm_volume += volume
    return {
        "source": "spy_option_chain_activity_proxy",
        "contracts": rows,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "put_call_volume_ratio": put_volume / call_volume if call_volume > 0 else None,
        "put_call_oi_ratio": put_oi / call_oi if call_oi > 0 else None,
        "delta_volume_normalized": signed_delta_volume / gross_delta_volume if gross_delta_volume > 0 else None,
        "near_atm_volume_fraction": near_atm_volume / total_volume if total_volume > 0 else None,
        "aggressor_side_available": False,
        "complex_order_classification_available": False,
        "sweep_detection_available": False,
        "interpretation": "activity_only_not_signed_order_flow",
    }

def _context_labels(context: MarketContext | None) -> tuple[str, str, str]:
    if context is None:
        return "neutral", "unknown", "normal"
    signals = context.signals
    risk = float(signals.get("risk_beta_spread") or 0.0)
    credit = signals.get("credit_impulse")
    dollar = signals.get("dollar_impulse")
    score = risk
    if credit is not None:
        score += 0.5 * float(credit)
    if dollar is not None:
        score -= 0.25 * float(dollar)
    risk_tone = "risk_on" if score > 0.0015 else "risk_off" if score < -0.0015 else "neutral"

    slope = signals.get("vix_term_slope")
    volatility_term = (
        "contango" if slope is not None and float(slope) > 0.025
        else "backwardation" if slope is not None and float(slope) < -0.025
        else "flat" if slope is not None
        else "unknown"
    )
    spread_bps = float(signals.get("average_spread_bps") or 0.0)
    liquidity = "thin" if spread_bps >= 2.5 else "normal"
    return risk_tone, volatility_term, liquidity


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
    """Classify one horizon using only feature rows timestamped before `timestamp`."""
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

    vol_hist = np.asarray([float(row["realized_vol"]) for row in rows if row["realized_vol"] is not None], dtype=float)
    corr_hist = np.asarray([float(row["correlation"]) for row in rows if row["correlation"] is not None], dtype=float)
    realized_vol = float(feature.get("realized_vol") or 0.0)
    current_corr = feature.get("correlation")

    if vol_hist.size >= 20:
        vol_q = float(np.mean(vol_hist <= realized_vol))
        volatility = "low" if vol_q < 0.25 else "normal" if vol_q < 0.75 else "high" if vol_q < 0.95 else "crisis"
    else:
        volatility = "normal"

    if current_corr is None or not np.isfinite(float(current_corr)):
        correlation = "unknown_correlation"
    else:
        current = float(current_corr)
        corr_q = float(np.mean(corr_hist <= current)) if corr_hist.size >= 20 else 0.5
        if corr_hist.size >= 20:
            recent_n = min(5, len(corr_hist))
            prior_n = min(20, len(corr_hist))
            recent = float(np.mean(corr_hist[:recent_n]))
            prior = float(np.mean(corr_hist[recent_n:prior_n])) if prior_n > recent_n else recent
            delta = recent - prior
        else:
            delta = 0.0
        correlation = (
            "dislocated" if corr_q > 0.95 or current >= 0.90
            else "rising" if delta > 0.03
            else "falling" if delta < -0.03
            else "stable"
        )

    breadth_value = float(feature.get("breadth") or 0.5)
    breadth = "broad_up" if breadth_value >= 0.70 else "broad_down" if breadth_value <= 0.30 else "mixed"
    concentration_value = float(feature.get("concentration") or 0.0)
    concentration = "concentrated" if concentration_value >= 0.18 else "distributed"

    local = timestamp.astimezone(ET).timetz().replace(tzinfo=None)
    session = "opening" if local < time(10, 0) else "midday" if local < time(15, 0) else "final_hour" if local < time(15, 50) else "expiration_window"

    allowed_events = {"ordinary", "earnings_heavy", "macro_announcement", "rebalance", "unknown"}
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
        "micro": classify_regime(journal, timestamp=timestamp, feature=feature, gamma_state=gamma_state, event_state=event_state, lookback=45, context=context),
        "intraday": classify_regime(journal, timestamp=timestamp, feature=feature, gamma_state=gamma_state, event_state=event_state, lookback=240, context=context),
        "swing": classify_regime(journal, timestamp=timestamp, feature=feature, gamma_state=gamma_state, event_state=event_state, lookback=780, context=context),
        "structural": classify_regime(journal, timestamp=timestamp, feature=feature, gamma_state=gamma_state, event_state=event_state, lookback=1950, context=context),
    }
    signs = []
    for state in levels.values():
        if state.breadth == "broad_up" or state.risk_tone == "risk_on":
            signs.append(1.0)
        elif state.breadth == "broad_down" or state.risk_tone == "risk_off":
            signs.append(-1.0)
        else:
            signs.append(0.0)
    conflict_score = float(np.std(signs))
    transition = conflict_score >= 0.65 or any(state.transition_risk for state in levels.values())
    return RegimeHierarchy(
        micro=levels["micro"],
        intraday=levels["intraday"],
        swing=levels["swing"],
        structural=levels["structural"],
        conflict_score=conflict_score,
        transition_risk=transition,
    )

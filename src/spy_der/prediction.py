from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import timedelta
from typing import Any

import numpy as np
from scipy.stats import norm

from .config import SuiteConfig
from .db import Journal
from .timeutil import ET, utc_now, utc_iso


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def classify_regime(feature: dict[str, Any], expected_return: float) -> str:
    breadth = float(feature["breadth"])
    pressure = float(feature["weighted_pressure"])
    concentration = float(feature["concentration"])
    dispersion = float(feature["dispersion"])
    corr = feature.get("correlation")
    direction = "BULLISH" if expected_return > 0.00035 else "BEARISH" if expected_return < -0.00035 else "NEUTRAL"
    participation = "BROAD" if breadth >= 0.62 or breadth <= 0.38 else "MIXED"
    structure = "CONCENTRATED" if concentration > 0.18 else "DISTRIBUTED"
    volatility = "EXPANSION" if dispersion > 0.012 or abs(pressure) > 1.8 else "NORMAL"
    correlation = "HIGH_CORR" if corr is not None and float(corr) > 0.70 else "LOW_CORR" if corr is not None and float(corr) < 0.35 else "MID_CORR"
    return f"{participation}_{direction}_{structure}_{volatility}_{correlation}"


def create_prediction(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    feature: dict[str, Any],
) -> dict[str, Any]:
    spy_price = float(snapshot.get("spy_price") or 0.0)
    if spy_price <= 0:
        raise RuntimeError("SPY price is unavailable")

    history = journal.quote_history("SPY", limit=config.prediction.return_lookback_rows)
    prices = np.asarray([float(row["price"]) for row in history if row.get("price")], dtype=float)
    momentum = 0.0
    mean_reversion = 0.0
    realized_sigma = float(feature["realized_vol"])
    if prices.size >= 3:
        log_returns = np.diff(np.log(prices))
        recent = log_returns[-min(5, len(log_returns)):]
        momentum = float(np.mean(recent)) if recent.size else 0.0
        if prices.size >= 20:
            short = float(np.mean(prices[-5:]))
            long = float(np.mean(prices[-20:]))
            mean_reversion = (long - short) / max(spy_price, 1e-9)
        if log_returns.size > 1:
            realized_sigma = float(np.std(log_returns, ddof=1))

    p = config.prediction
    constituent_signal = (
        p.pressure_coefficient * float(feature["weighted_return"])
        + p.residual_coefficient * float(feature["residual_pressure"]) * max(float(feature["dispersion"]), 0.0001)
        + p.breadth_coefficient * (float(feature["breadth"]) - 0.5) * max(float(feature["dispersion"]), 0.0001)
    )
    expected_return = constituent_signal + 0.55 * momentum + p.mean_reversion_coefficient * mean_reversion
    expected_return = float(max(-0.025, min(0.025, expected_return)))

    horizon_scale = math.sqrt(max(p.horizon_minutes, 1) / max(config.market.poll_interval_seconds / 60, 1))
    sigma_return = float(
        max(p.min_sigma_return, min(p.max_sigma_return, realized_sigma * horizon_scale))
    )
    z = p.distribution_z
    predicted_price = spy_price * math.exp(expected_return)
    predicted_low = spy_price * math.exp(expected_return - z * sigma_return)
    predicted_high = spy_price * math.exp(expected_return + z * sigma_return)
    probability_up = float(norm.cdf(expected_return / max(sigma_return, 1e-9)))
    regime = classify_regime(feature, expected_return)

    now = utc_now()
    target = now + timedelta(minutes=p.horizon_minutes)
    config_fingerprint = {
        "prediction": config.prediction.model_dump(),
        "strategy": config.strategy.model_dump(),
        "risk": config.risk.model_dump(),
    }
    feature_fingerprint = {
        k: v
        for k, v in feature.items()
        if k not in {"payload", "created_at"}
    }
    return {
        "prediction_id": f"P-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:10]}",
        "snapshot_id": snapshot["snapshot_id"],
        "created_at": utc_iso(now),
        "target_at": utc_iso(target),
        "horizon_minutes": p.horizon_minutes,
        "formal_anchor": now.astimezone(ET).minute % max(config.audit.formal_anchor_minutes, 1) == 0,
        "spy_price": spy_price,
        "predicted_price": predicted_price,
        "predicted_low": predicted_low,
        "predicted_high": predicted_high,
        "probability_up": probability_up,
        "expected_return": expected_return,
        "sigma_return": sigma_return,
        "regime": regime,
        "model_version": p.model_version,
        "config_hash": canonical_hash(config_fingerprint),
        "feature_hash": canonical_hash(feature_fingerprint),
        "integrity": "PENDING",
        "payload": {
            "momentum": momentum,
            "mean_reversion": mean_reversion,
            "constituent_signal": constituent_signal,
            "feature_snapshot": feature_fingerprint,
        },
    }

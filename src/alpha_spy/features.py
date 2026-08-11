from __future__ import annotations

import math
from typing import Any

import numpy as np

from .config import SuiteConfig
from .db import Journal
from .timeutil import utc_iso


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_features(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    quotes: list[dict[str, Any]],
) -> dict[str, Any]:
    constituents = [
        q
        for q in quotes
        if q.get("symbol") != "SPY"
        and q.get("price") is not None
        and float(q.get("weight", 0.0)) > 0
    ]
    if not constituents:
        raise RuntimeError("No constituent quotes available for feature computation")

    weights = np.asarray([float(q.get("weight", 0.0)) for q in constituents], dtype=float)
    returns = np.asarray([float(q.get("change_pct") or 0.0) for q in constituents], dtype=float)
    valid = np.isfinite(weights) & np.isfinite(returns) & (weights > 0)
    weights = weights[valid]
    returns = returns[valid]
    if weights.size == 0:
        raise RuntimeError("No valid weighted constituent returns")
    weight_sum = float(weights.sum())
    normalized = weights / max(weight_sum, 1e-12)

    weighted_return = float(np.dot(normalized, returns))
    breadth = float(np.dot(normalized, (returns > 0).astype(float)))
    dispersion = float(np.sqrt(np.dot(normalized, np.square(returns - weighted_return))))
    signed_pressure = float(weighted_return / max(dispersion, 1e-5))

    contributions = normalized * returns
    absolute = np.abs(contributions)
    if float(absolute.sum()) > 0:
        shares = absolute / absolute.sum()
        concentration = float(np.square(shares).sum())
    else:
        concentration = float(np.square(normalized).sum())

    spy_history = journal.quote_history("SPY", limit=max(30, config.prediction.return_lookback_rows))
    spy_prices = np.asarray([float(x["price"]) for x in spy_history if x.get("price")], dtype=float)
    realized_vol = config.prediction.min_sigma_return
    spy_returns = np.asarray([], dtype=float)
    if spy_prices.size >= 3:
        spy_returns = np.diff(np.log(spy_prices))
        if spy_returns.size:
            realized_vol = float(np.std(spy_returns, ddof=1))
    realized_vol = _clip(
        realized_vol,
        config.prediction.min_sigma_return,
        config.prediction.max_sigma_return,
    )

    # Compare current constituent return to SPY session return as a cross-sectional residual proxy.
    spy_quote = next((q for q in quotes if q.get("symbol") == "SPY"), None)
    spy_session_return = float((spy_quote or {}).get("change_pct") or 0.0)
    residuals = returns - spy_session_return
    residual_pressure = float(np.dot(normalized, residuals) / max(dispersion, 1e-5))

    # Rolling correlation approximation from recent weighted-return features versus SPY returns.
    correlation = None
    downside_correlation = None
    with journal.connect() as con:
        rows = con.execute(
            """
            SELECT f.weighted_return, s.spy_price
            FROM features f
            JOIN market_snapshots s ON s.snapshot_id=f.snapshot_id
            WHERE s.spy_price IS NOT NULL
            ORDER BY f.created_at DESC LIMIT 120
            """
        ).fetchall()
    if len(rows) >= 8:
        wr = np.asarray([float(r["weighted_return"]) for r in reversed(rows)], dtype=float)
        sp = np.asarray([float(r["spy_price"]) for r in reversed(rows)], dtype=float)
        sr = np.diff(np.log(sp))
        wr = wr[-len(sr):]
        if len(sr) >= 4 and np.std(wr) > 1e-12 and np.std(sr) > 1e-12:
            correlation = float(np.corrcoef(wr, sr)[0, 1])
            mask = sr < 0
            if mask.sum() >= 4 and np.std(wr[mask]) > 1e-12 and np.std(sr[mask]) > 1e-12:
                downside_correlation = float(np.corrcoef(wr[mask], sr[mask])[0, 1])

    covered_weight = float(snapshot.get("covered_weight", 0.0))
    stale_fraction = float(snapshot.get("stale_quote_count", 0)) / max(float(snapshot.get("quote_count", 1)), 1.0)
    is_demo = snapshot.get("source") == "demo"
    universe_factor = (
        1.0
        if is_demo
        else _clip(len(constituents) / max(config.universe.minimum_symbols, 1), 0.0, 1.0)
    )
    coverage_factor = (
        1.0
        if is_demo
        else _clip(covered_weight / max(config.universe.minimum_covered_weight, 1e-9), 0.0, 1.0)
    )
    freshness_factor = _clip(1.0 - stale_fraction * 2.0, 0.0, 1.0)
    source_factor = (
        1.0
        if snapshot.get("source") in {"tradier", "tradier_production_stream"}
        else 0.90
        if is_demo
        else 0.72
    )
    stability_factor = 1.0
    if correlation is not None and not math.isfinite(correlation):
        stability_factor = 0.5
    trust_score = float(
        _clip(
            universe_factor
            * coverage_factor
            * freshness_factor
            * source_factor
            * stability_factor,
            0.0,
            1.0,
        )
    )
    if trust_score >= config.audit.health_green:
        health_state = "GREEN"
    elif trust_score >= config.audit.health_yellow:
        health_state = "YELLOW"
    elif trust_score >= config.audit.health_orange:
        health_state = "ORANGE"
    else:
        health_state = "RED"

    surface = journal.surface_metrics(snapshot["snapshot_id"]) or {}

    top_attribution = sorted(
        [
            {
                "symbol": q["symbol"],
                "weight": float(q.get("weight", 0.0)),
                "return": float(q.get("change_pct") or 0.0),
                "contribution": float(q.get("weight", 0.0)) * float(q.get("change_pct") or 0.0),
                "sector": q.get("sector") or "Unknown",
            }
            for q in constituents
        ],
        key=lambda x: abs(x["contribution"]),
        reverse=True,
    )[:25]

    return {
        "snapshot_id": snapshot["snapshot_id"],
        "created_at": utc_iso(),
        "breadth": breadth,
        "weighted_pressure": signed_pressure,
        "concentration": concentration,
        "dispersion": dispersion,
        "correlation": correlation,
        "downside_correlation": downside_correlation,
        "weighted_return": weighted_return,
        "residual_pressure": residual_pressure,
        "realized_vol": realized_vol,
        "trust_score": trust_score,
        "health_state": health_state,
        "payload": {
            "constituent_count": len(constituents),
            "covered_weight": covered_weight,
            "stale_fraction": stale_fraction,
            "spy_session_return": spy_session_return,
            "top_attribution": top_attribution,
            "surface": {
                "reference_expiration": surface.get("reference_expiration"),
                "spy_iv": surface.get("spy_iv"),
                "constituent_iv": surface.get("constituent_iv"),
                "vol_gap": surface.get("vol_gap"),
                "spy_skew": surface.get("spy_skew"),
                "constituent_skew": surface.get("constituent_skew"),
                "skew_gap": surface.get("skew_gap"),
                "covered_weight": surface.get("covered_weight"),
                "symbol_count": surface.get("symbol_count"),
                "integrity": surface.get("integrity"),
            },
        },
    }

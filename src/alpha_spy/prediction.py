from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any

import numpy as np
from scipy.stats import norm
from sklearn.ensemble import HistGradientBoostingRegressor

from .config import ForecastHorizonSpec, SuiteConfig
from .context import MarketContext, build_market_context
from .db import Journal
from .distributions import build_distribution_bundle, simulate_path_metrics
from .events import EventState, event_state_at
from .regime import RegimeHierarchy, classify_regime_hierarchy
from .timeutil import ET, utc_iso
from .trading_calendar import MarketCalendarResolver


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed



def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if np.isfinite(parsed) else default


def _ridge_vector_from_parts(
    *,
    feature: dict[str, Any],
    context_signals: dict[str, Any],
    option_activity: dict[str, Any] | None,
    momentum: float,
    mean_reversion: float,
) -> tuple[list[str], np.ndarray]:
    option_activity = option_activity or {}
    names = [
        "momentum",
        "mean_reversion",
        "weighted_return",
        "residual_pressure",
        "breadth_centered",
        "concentration",
        "dispersion",
        "realized_vol",
        "correlation",
        "downside_correlation",
        "risk_beta_spread",
        "credit_impulse",
        "dollar_impulse",
        "rates_curve_proxy",
        "vix_term_slope",
        "sector_breadth_centered",
        "sector_dispersion",
        "order_flow_imbalance",
        "average_spread_bps",
        "large_trade_ratio",
        "internal_tick_normalized",
        "advance_decline_normalized",
        "log_trin_proxy",
        "auction_vwap_distance_bps",
        "opening_range_position_centered",
        "option_put_call_volume_log",
        "option_put_call_oi_log",
        "option_delta_volume_normalized",
        "option_near_atm_volume_fraction",
    ]
    trin = _finite(context_signals.get("trin_proxy"), 1.0)
    pcv = _finite(option_activity.get("put_call_volume_ratio"), 1.0)
    pcoi = _finite(option_activity.get("put_call_oi_ratio"), 1.0)
    sector_breadth = context_signals.get("sector_breadth")
    opening = context_signals.get("opening_range_position")
    values = np.asarray(
        [
            momentum,
            mean_reversion,
            _finite(feature.get("weighted_return")),
            _finite(feature.get("residual_pressure")),
            _finite(feature.get("breadth"), 0.5) - 0.5,
            _finite(feature.get("concentration")),
            _finite(feature.get("dispersion")),
            _finite(feature.get("realized_vol")),
            _finite(feature.get("correlation")),
            _finite(feature.get("downside_correlation")),
            _finite(context_signals.get("risk_beta_spread")),
            _finite(context_signals.get("credit_impulse")),
            _finite(context_signals.get("dollar_impulse")),
            _finite(context_signals.get("rates_curve_proxy")),
            _finite(context_signals.get("vix_term_slope")),
            _finite(sector_breadth, 0.5) - 0.5 if sector_breadth is not None else 0.0,
            _finite(context_signals.get("sector_dispersion")),
            _finite(context_signals.get("order_flow_imbalance")),
            _finite(context_signals.get("average_spread_bps")),
            _finite(context_signals.get("large_trade_ratio")),
            _finite(context_signals.get("internal_tick_normalized")),
            _finite(context_signals.get("advance_decline_normalized")),
            math.log(max(trin, 1e-6)),
            _finite(context_signals.get("auction_vwap_distance_bps")),
            _finite(opening, 0.5) - 0.5 if opening is not None else 0.0,
            math.log(max(pcv, 1e-6)),
            math.log(max(pcoi, 1e-6)),
            _finite(option_activity.get("delta_volume_normalized")),
            _finite(option_activity.get("near_atm_volume_fraction")),
        ],
        dtype=float,
    )
    return names, values


def _ridge_vector_from_prediction_payload(payload: dict[str, Any]) -> tuple[list[str], np.ndarray]:
    feature = payload.get("feature_snapshot", {}) if isinstance(payload, dict) else {}
    market_context = payload.get("market_context", {}) if isinstance(payload, dict) else {}
    signals = market_context.get("signals", {}) if isinstance(market_context, dict) else {}
    option_activity = payload.get("option_activity", {}) if isinstance(payload, dict) else {}
    return _ridge_vector_from_parts(
        feature=feature if isinstance(feature, dict) else {},
        context_signals=signals if isinstance(signals, dict) else {},
        option_activity=option_activity if isinstance(option_activity, dict) else {},
        momentum=_finite(payload.get("momentum")),
        mean_reversion=_finite(payload.get("mean_reversion")),
    )



def _is_formal_anchor(config: SuiteConfig, timestamp: datetime, horizon_minutes: int) -> bool:
    local = timestamp.astimezone(ET)
    since_open = local.hour * 60 + local.minute - (9 * 60 + 30)
    if since_open < 0:
        return False
    period = max(config.audit.formal_anchor_minutes, min(max(horizon_minutes, 1), 390))
    return since_open % period == 0


def _walk_forward_shadow_signal(
    journal: Journal,
    config: SuiteConfig,
    *,
    horizon_name: str,
    horizon_minutes: int,
    as_of: datetime,
    current_names: list[str],
    current_vector: np.ndarray,
) -> dict[str, Any]:
    """Nonlinear champion/challenger inference with zero trading authority."""
    if not config.prediction.shadow_model_enabled or horizon_name not in {"15m", "30m"}:
        return {"mode": "disabled", "trading_authority": False}
    if not _is_formal_anchor(config, as_of, horizon_minutes):
        return {"mode": "not_formal_anchor", "trading_authority": False}
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT p.payload_json,o.actual_return
            FROM prediction_outcomes o
            JOIN predictions p ON p.prediction_id=o.prediction_id
            WHERE o.integrity IN ('VERIFIED','MINOR_REVISION')
              AND o.confirmed_at <= ?
              AND p.horizon_minutes=?
              AND p.formal_anchor=1
              AND p.model_version=?
              AND p.config_hash=?
            ORDER BY o.confirmed_at DESC
            LIMIT ?
            """,
            (
                utc_iso(as_of),
                int(horizon_minutes),
                config.prediction.model_version,
                config.decision_fingerprint(),
                int(config.prediction.shadow_model_lookback),
            ),
        ).fetchall()
    vectors: list[np.ndarray] = []
    targets: list[float] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            names, vector = _ridge_vector_from_prediction_payload(payload)
            target = float(row["actual_return"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if names != current_names or not np.all(np.isfinite(vector)) or not np.isfinite(target):
            continue
        vectors.append(vector)
        targets.append(float(np.clip(target, -0.08, 0.08)))
    n = len(vectors)
    if n < config.prediction.shadow_model_min_samples:
        return {
            "mode": "shadow_cold_start",
            "samples": n,
            "required_samples": config.prediction.shadow_model_min_samples,
            "trading_authority": False,
        }
    x = np.vstack(vectors)
    y = np.asarray(targets, dtype=float)
    model = HistGradientBoostingRegressor(
        loss="squared_error",
        max_iter=config.prediction.shadow_model_max_iter,
        learning_rate=0.04,
        max_leaf_nodes=15,
        min_samples_leaf=25,
        l2_regularization=3.0,
        random_state=20260810 + int(horizon_minutes),
    )
    model.fit(x, y)
    predicted = float(model.predict(current_vector.reshape(1, -1))[0])
    limit = min(0.08, 0.012 * math.sqrt(max(horizon_minutes, 5) / 15.0))
    predicted = float(np.clip(predicted, -limit, limit))
    return {
        "mode": "walk_forward_hist_gradient_boosting_shadow",
        "samples": n,
        "expected_return": predicted,
        "feature_names": current_names,
        "training_cutoff": utc_iso(as_of),
        "training_sampling": "matured_non_overlapping_formal_anchors_only",
        "trading_authority": False,
        "affects_risk": False,
        "affects_promotion": False,
    }

def _walk_forward_ridge_signal(
    journal: Journal,
    config: SuiteConfig,
    *,
    horizon_name: str,
    horizon_minutes: int,
    as_of: datetime,
    current_names: list[str],
    current_vector: np.ndarray,
) -> tuple[float | None, dict[str, Any]]:
    if not config.prediction.ridge_enabled:
        return None, {"mode": "disabled", "samples": 0}
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT p.payload_json,o.actual_return
            FROM prediction_outcomes o
            JOIN predictions p ON p.prediction_id=o.prediction_id
            WHERE o.integrity IN ('VERIFIED','MINOR_REVISION')
              AND o.confirmed_at <= ?
              AND p.horizon_minutes=?
              AND p.formal_anchor=1
              AND p.model_version=?
              AND p.config_hash=?
            ORDER BY o.confirmed_at DESC
            LIMIT ?
            """,
            (
                utc_iso(as_of),
                int(horizon_minutes),
                config.prediction.model_version,
                config.decision_fingerprint(),
                int(config.prediction.ridge_lookback),
            ),
        ).fetchall()
    vectors: list[np.ndarray] = []
    targets: list[float] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            names, vector = _ridge_vector_from_prediction_payload(payload)
            target = float(row["actual_return"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if names != current_names or not np.all(np.isfinite(vector)) or not np.isfinite(target):
            continue
        vectors.append(vector)
        targets.append(float(np.clip(target, -0.08, 0.08)))
    n = len(vectors)
    if n < config.prediction.ridge_min_samples:
        return None, {
            "mode": "heuristic_cold_start",
            "samples": n,
            "required_samples": config.prediction.ridge_min_samples,
        }

    x = np.vstack(vectors)
    y = np.asarray(targets, dtype=float)
    x_mean = np.mean(x, axis=0)
    x_std = np.std(x, axis=0, ddof=1)
    x_std = np.where(np.isfinite(x_std) & (x_std > 1e-10), x_std, 1.0)
    z = (x - x_mean) / x_std
    current_z = (current_vector - x_mean) / x_std
    y_mean = float(np.mean(y))
    yc = y - y_mean
    alpha = max(float(config.prediction.ridge_alpha_by_horizon.get(horizon_name, 1.0)), 0.0)
    gram = z.T @ z + alpha * np.eye(z.shape[1])
    try:
        beta = np.linalg.solve(gram, z.T @ yc)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(gram) @ (z.T @ yc)
    predicted = float(y_mean + current_z @ beta)
    # Do not permit an online fit to manufacture a physically implausible minute return.
    limit = min(0.08, 0.012 * math.sqrt(max(horizon_minutes, 5) / 15.0))
    predicted = float(np.clip(predicted, -limit, limit))
    fitted = y_mean + z @ beta
    residual_sigma = float(np.std(y - fitted, ddof=1)) if n > 2 else None
    return predicted, {
        "mode": "walk_forward_ridge",
        "samples": n,
        "alpha": alpha,
        "feature_names": current_names,
        "intercept_standardized": y_mean,
        "coefficients_standardized": [float(value) for value in beta],
        "residual_sigma": residual_sigma,
        "training_cutoff": utc_iso(as_of),
        "training_sampling": "matured_non_overlapping_formal_anchors_only",
    }

def _regime_calibration(
    journal: Journal,
    config: SuiteConfig,
    *,
    regime: str,
    horizon_minutes: int,
    raw_expected_return: float,
    base_sigma: float,
    as_of: datetime,
) -> tuple[float, float, dict[str, Any]]:
    """Walk-forward calibration: only outcomes confirmed before this forecast exist."""
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT p.expected_return AS forecast_return,
                   p.regime AS regime,
                   p.horizon_minutes AS horizon_minutes,
                   o.actual_return AS actual_return
            FROM prediction_outcomes o
            JOIN predictions p ON p.prediction_id=o.prediction_id
            WHERE o.integrity IN ('VERIFIED','MINOR_REVISION')
              AND o.confirmed_at <= ?
              AND p.horizon_minutes = ?
              AND p.model_version = ?
              AND p.config_hash = ?
            ORDER BY o.confirmed_at DESC
            LIMIT ?
            """,
            (
                utc_iso(as_of),
                int(horizon_minutes),
                config.prediction.model_version,
                config.decision_fingerprint(),
                max(100, int(config.prediction.calibration_lookback)),
            ),
        ).fetchall()

    exact = [row for row in rows if str(row["regime"]) == regime]
    if len(exact) >= config.prediction.regime_calibration_min_samples:
        selected = exact
        mode = "regime"
    elif len(rows) >= config.prediction.global_calibration_min_samples:
        selected = list(rows)
        mode = "global"
    else:
        return raw_expected_return, base_sigma, {
            "mode": "bootstrap",
            "samples": len(exact),
            "global_samples": len(rows),
            "slope": 1.0,
            "intercept": 0.0,
            "residual_sigma": None,
        }

    x = np.asarray([float(row["forecast_return"]) for row in selected], dtype=float)
    y = np.asarray([float(row["actual_return"]) for row in selected], dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) < 10:
        return raw_expected_return, base_sigma, {
            "mode": "bootstrap",
            "samples": len(x),
            "slope": 1.0,
            "intercept": 0.0,
            "residual_sigma": None,
        }

    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    centered = x - x_mean
    slope = float(np.dot(centered, y - y_mean) / (np.dot(centered, centered) + 1e-8))
    slope = float(np.clip(slope, 0.20, 2.0))
    limit = 0.0020 * math.sqrt(max(horizon_minutes, 5) / 15.0)
    intercept = float(np.clip(y_mean - slope * x_mean, -limit, limit))
    fitted = intercept + slope * x
    residual_sigma = float(np.std(y - fitted, ddof=1)) if len(x) > 2 else 0.0
    calibrated = float(intercept + slope * raw_expected_return)
    sigma = max(base_sigma, residual_sigma)
    return calibrated, sigma, {
        "mode": mode,
        "samples": len(x),
        "slope": slope,
        "intercept": intercept,
        "residual_sigma": residual_sigma,
    }


def _transition_adjustment(hierarchy: RegimeHierarchy) -> tuple[float, float]:
    regime = hierarchy.intraday
    mean_scale = 1.0
    sigma_scale = 1.0
    if regime.volatility == "high":
        mean_scale *= 0.90
        sigma_scale *= 1.10
    elif regime.volatility == "crisis":
        mean_scale *= 0.65
        sigma_scale *= 1.35
    if regime.correlation in {"rising", "dislocated", "unknown_correlation"}:
        mean_scale *= 0.90
        sigma_scale *= 1.10
    if regime.dealer_gamma == "negative_gamma":
        sigma_scale *= 1.10
    elif regime.dealer_gamma == "unknown_gamma":
        mean_scale *= 0.95
        sigma_scale *= 1.05
    if regime.event in {"macro_announcement", "rebalance"}:
        mean_scale *= 0.75
        sigma_scale *= 1.25
    elif regime.event == "unknown":
        mean_scale *= 0.95
        sigma_scale *= 1.05
    if regime.volatility_term == "backwardation":
        mean_scale *= 0.90
        sigma_scale *= 1.15
    if regime.liquidity == "thin":
        mean_scale *= 0.85
        sigma_scale *= 1.15
    if hierarchy.conflict_score >= 0.65:
        mean_scale *= 0.80
        sigma_scale *= 1.15
    return mean_scale, sigma_scale


def _base_signal(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    feature: dict[str, Any],
    context: MarketContext,
    horizon_minutes: int,
    horizon_name: str,
) -> tuple[float, float, dict[str, Any]]:
    spy_price = float(snapshot.get("spy_price") or 0.0)
    history = journal.quote_history_as_of(
        "SPY", str(snapshot["captured_at"]), limit=config.prediction.return_lookback_rows
    )
    prices = np.asarray([float(row["price"]) for row in history if row.get("price")], dtype=float)
    momentum = 0.0
    mean_reversion = 0.0
    realized_sigma = float(feature["realized_vol"])
    if prices.size >= 3:
        log_returns = np.diff(np.log(prices))
        recent_n = max(2, min(len(log_returns), max(3, horizon_minutes // 5)))
        recent = log_returns[-recent_n:]
        momentum = float(np.mean(recent)) if recent.size else 0.0
        if prices.size >= 20:
            short = float(np.mean(prices[-5:]))
            long = float(np.mean(prices[-20:]))
            mean_reversion = (long - short) / max(spy_price, 1e-9)
        if log_returns.size > 1:
            realized_sigma = float(np.std(log_returns[-min(len(log_returns), 120):], ddof=1))

    p = config.prediction
    constituent_signal = (
        p.pressure_coefficient * float(feature["weighted_return"])
        + p.residual_coefficient * float(feature["residual_pressure"]) * max(float(feature["dispersion"]), 0.0001)
        + p.breadth_coefficient * (float(feature["breadth"]) - 0.5) * max(float(feature["dispersion"]), 0.0001)
    )
    ctx = context.signals
    context_signal = (
        0.15 * float(ctx.get("risk_beta_spread") or 0.0)
        + 0.08 * float(ctx.get("credit_impulse") or 0.0)
        - 0.04 * float(ctx.get("dollar_impulse") or 0.0)
        + 0.00020 * float(ctx.get("order_flow_imbalance") or 0.0)
        + 0.00012 * float(ctx.get("internal_tick_normalized") or 0.0)
        + 0.00008 * float(ctx.get("advance_decline_normalized") or 0.0)
        + 0.00005 * math.tanh(float(ctx.get("auction_vwap_distance_bps") or 0.0) / 20.0)
    )
    per_15 = constituent_signal + 0.55 * momentum + p.mean_reversion_coefficient * mean_reversion + context_signal
    horizon_ratio = max(horizon_minutes, 1) / 15.0
    # Alpha scales slower than linearly at long horizons; volatility scales with sqrt(time).
    alpha_scale = horizon_ratio if horizon_ratio <= 2.0 else 2.0 * math.sqrt(horizon_ratio / 2.0)
    heuristic_expected = float(np.clip(per_15 * alpha_scale, -0.08, 0.08))

    option_activity = feature.get("payload", {}).get("hardening", {}).get("option_activity", {})
    vector_names, current_vector = _ridge_vector_from_parts(
        feature=feature,
        context_signals=ctx,
        option_activity=option_activity if isinstance(option_activity, dict) else {},
        momentum=momentum,
        mean_reversion=mean_reversion,
    )
    signal_as_of = _parse_timestamp(str(snapshot["captured_at"]))
    ridge_expected, ridge = _walk_forward_ridge_signal(
        journal,
        config,
        horizon_name=horizon_name,
        horizon_minutes=horizon_minutes,
        as_of=signal_as_of,
        current_names=vector_names,
        current_vector=current_vector,
    )
    shadow = _walk_forward_shadow_signal(
        journal,
        config,
        horizon_name=horizon_name,
        horizon_minutes=horizon_minutes,
        as_of=signal_as_of,
        current_names=vector_names,
        current_vector=current_vector,
    )
    raw_expected = ridge_expected if ridge_expected is not None else heuristic_expected
    base_sigma = float(
        np.clip(
            realized_sigma * math.sqrt(max(horizon_minutes, 1)),
            p.min_sigma_return,
            p.max_sigma_return,
        )
    )
    return raw_expected, base_sigma, {
        "momentum": momentum,
        "mean_reversion": mean_reversion,
        "constituent_signal": constituent_signal,
        "context_signal": context_signal,
        "heuristic_expected_return": heuristic_expected,
        "signal_model": ridge,
        "shadow_model": shadow,
        "raw_expected_return": raw_expected,
    }


def _prediction_id(snapshot_id: str, horizon_name: str, model_version: str) -> str:
    digest = hashlib.sha256(f"{snapshot_id}|{horizon_name}|{model_version}".encode()).hexdigest()[:12]
    return f"P-{snapshot_id}-{horizon_name}-{digest}"


def _single_prediction(
    journal: Journal,
    config: SuiteConfig,
    *,
    snapshot: dict[str, Any],
    quotes: list[dict[str, Any]],
    feature: dict[str, Any],
    context: MarketContext,
    event: EventState,
    hierarchy: RegimeHierarchy,
    spec: ForecastHorizonSpec,
    target_at_override: datetime | None = None,
    calendar_meta_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spy_price = float(snapshot.get("spy_price") or 0.0)
    if spy_price <= 0:
        raise RuntimeError("SPY price is unavailable")
    created_at = _parse_timestamp(str(snapshot["captured_at"]))
    if target_at_override is not None:
        target_at = target_at_override
        calendar_meta = dict(calendar_meta_override or {})
        calendar_meta.setdefault("source", "frozen_replay")
        calendar_meta.setdefault("effective_minutes", spec.minutes)
    else:
        target_at, calendar_meta = MarketCalendarResolver(config, journal).target(created_at, spec)
    effective_minutes = max(1, int(calendar_meta.get("effective_minutes") or spec.minutes))

    raw_expected, base_sigma, base_parts = _base_signal(
        journal, config, snapshot, feature, context, effective_minutes, spec.name
    )
    calibrated_return, calibrated_sigma, calibration = _regime_calibration(
        journal,
        config,
        regime=hierarchy.key,
        horizon_minutes=spec.minutes,
        raw_expected_return=raw_expected,
        base_sigma=base_sigma,
        as_of=created_at,
    )
    mean_scale, sigma_scale = _transition_adjustment(hierarchy)
    deterministic_expected = float(np.clip(calibrated_return * mean_scale, -0.08, 0.08))
    deterministic_sigma = float(
        np.clip(calibrated_sigma * sigma_scale, config.prediction.min_sigma_return, config.prediction.max_sigma_return)
    )

    distribution = build_distribution_bundle(
        journal,
        config,
        snapshot=snapshot,
        quotes=quotes,
        horizon_minutes=effective_minutes,
        fallback_expected_return=deterministic_expected,
        fallback_sigma_return=deterministic_sigma,
    )
    p_returns = np.asarray(distribution.physical.returns, dtype=float)
    p_returns = p_returns[np.isfinite(p_returns)]
    if not p_returns.size:
        p_returns = np.asarray([deterministic_expected], dtype=float)
    p_expected_simple = float(np.mean(p_returns))
    p_sigma = float(np.std(p_returns, ddof=1)) if p_returns.size > 1 else deterministic_sigma
    expected_return = float(np.clip(math.log1p(max(p_expected_simple, -0.95)), -0.08, 0.08))
    sigma_return = float(np.clip(max(p_sigma, deterministic_sigma * 0.70), config.prediction.min_sigma_return, config.prediction.max_sigma_return))

    prices = np.asarray(distribution.physical.prices, dtype=float)
    predicted_price = float(np.mean(prices))
    predicted_low = float(np.quantile(prices, 0.10))
    predicted_high = float(np.quantile(prices, 0.90))
    probability_up = float(np.mean(prices > spy_price))
    if not np.isfinite(probability_up):
        probability_up = float(norm.cdf(expected_return / max(sigma_return, 1e-9)))

    gamma_state = hierarchy.intraday.dealer_gamma
    path = simulate_path_metrics(
        snapshot_id=str(snapshot["snapshot_id"]),
        horizon_minutes=effective_minutes,
        expected_return=expected_return,
        sigma_return=sigma_return,
        gamma_state=gamma_state,
        breadth=float(feature.get("breadth") or 0.5),
        order_flow_imbalance=float(context.signals.get("order_flow_imbalance") or 0.0),
    )
    surface = feature.get("payload", {}).get("surface", {})
    vol_gap = surface.get("vol_gap")
    iv_change_forecast = (
        -0.25 * float(vol_gap)
        if vol_gap is not None
        else 0.0
    )
    if hierarchy.intraday.volatility in {"high", "crisis"}:
        iv_change_forecast += 0.01
    if hierarchy.intraday.volatility_term == "backwardation":
        iv_change_forecast += 0.015
    if hierarchy.intraday.dealer_gamma == "negative_gamma":
        iv_change_forecast += 0.005

    model_uncertainty = distribution.model_uncertainty
    model_uncertainty += max(0.0, config.context.minimum_context_coverage - context.coverage) * 0.01
    if hierarchy.transition_risk:
        model_uncertainty += 0.0025
    model_uncertainty = float(min(model_uncertainty, config.prediction.max_model_uncertainty))

    feature_fingerprint = {k: v for k, v in feature.items() if k not in {"payload", "created_at"}}
    return {
        "prediction_id": _prediction_id(str(snapshot["snapshot_id"]), spec.name, config.prediction.model_version),
        "snapshot_id": snapshot["snapshot_id"],
        "created_at": utc_iso(created_at),
        "target_at": utc_iso(target_at),
        "horizon_minutes": spec.minutes,
        "formal_anchor": _is_formal_anchor(config, created_at, spec.minutes),
        "spy_price": spy_price,
        "predicted_price": predicted_price,
        "predicted_low": predicted_low,
        "predicted_high": predicted_high,
        "probability_up": probability_up,
        "expected_return": expected_return,
        "sigma_return": sigma_return,
        "regime": hierarchy.key,
        "model_version": config.prediction.model_version,
        "config_hash": config.decision_fingerprint(),
        "feature_hash": canonical_hash(feature_fingerprint),
        "integrity": "PENDING",
        "payload": {
            **base_parts,
            "horizon_name": spec.name,
            "horizon_role": spec.role,
            "trade_eligible": spec.trade_eligible,
            "effective_horizon_minutes": effective_minutes,
            "calendar": calendar_meta,
            "calibration": calibration,
            "transition_adjustment": {"mean_scale": mean_scale, "sigma_scale": sigma_scale},
            "regime_hierarchy": hierarchy.as_dict(),
            "regime_state": hierarchy.intraday.as_dict(),
            "event_state": event.as_dict(),
            "market_context": context.as_dict(),
            "option_activity": feature.get("payload", {}).get("hardening", {}).get("option_activity", {}),
            "distribution": distribution.as_dict(),
            "path": path.as_dict(),
            "forecast_iv_change": iv_change_forecast,
            "model_uncertainty": model_uncertainty,
            "feature_snapshot": feature_fingerprint,
            "surface_snapshot": {
                "reference_expiration": surface.get("reference_expiration"),
                "spy_iv": surface.get("spy_iv"),
                "constituent_iv": surface.get("constituent_iv"),
                "vol_gap": surface.get("vol_gap"),
                "spy_skew": surface.get("spy_skew"),
                "constituent_skew": surface.get("constituent_skew"),
                "skew_gap": surface.get("skew_gap"),
                "covered_weight": surface.get("covered_weight"),
                "integrity": surface.get("integrity"),
            },
            "input_health": feature.get("payload", {}).get("hardening", {}).get("input_health", {}),
        },
    }



def create_prediction_for_horizon(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    feature: dict[str, Any],
    spec: ForecastHorizonSpec,
    *,
    quotes: list[dict[str, Any]] | None = None,
    frozen_event: EventState | None = None,
    frozen_target_at: datetime | None = None,
    frozen_calendar_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute exactly one forecast horizon from timestamped stored inputs.

    The helper exists for deterministic captured-tape replay.  Market context and
    hierarchical regime are rebuilt from the frozen database as-of the snapshot;
    only the external event-calendar state and calendar target may be supplied from
    the original prediction so a later calendar-file refresh cannot rewrite history.
    """
    quotes = quotes if quotes is not None else journal.snapshot_quotes(str(snapshot["snapshot_id"]))
    context = build_market_context(journal, config, snapshot, quotes)
    hardening = feature.get("payload", {}).get("hardening", {})
    gamma_state = str(hardening.get("gamma", {}).get("state") or "unknown_gamma")
    created_at = _parse_timestamp(str(snapshot["captured_at"]))
    event = frozen_event or event_state_at(config, created_at)
    hierarchy = classify_regime_hierarchy(
        journal,
        timestamp=created_at,
        feature=feature,
        gamma_state=gamma_state,
        event_state=event.state,
        context=context,
    )
    return _single_prediction(
        journal,
        config,
        snapshot=snapshot,
        quotes=quotes,
        feature=feature,
        context=context,
        event=event,
        hierarchy=hierarchy,
        spec=spec,
        target_at_override=frozen_target_at,
        calendar_meta_override=frozen_calendar_meta,
    )

def create_prediction_bundle(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    feature: dict[str, Any],
    *,
    quotes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    quotes = quotes if quotes is not None else journal.snapshot_quotes(str(snapshot["snapshot_id"]))
    context = build_market_context(journal, config, snapshot, quotes)
    hardening = feature.setdefault("payload", {}).setdefault("hardening", {})
    gamma_state = str(hardening.get("gamma", {}).get("state") or "unknown_gamma")
    created_at = _parse_timestamp(str(snapshot["captured_at"]))
    event = event_state_at(config, created_at)
    hierarchy = classify_regime_hierarchy(
        journal,
        timestamp=created_at,
        feature=feature,
        gamma_state=gamma_state,
        event_state=event.state,
        context=context,
    )
    hardening["market_context"] = context.as_dict()
    hardening["event_state"] = event.state
    hardening["event"] = event.as_dict()
    hardening["regime_hierarchy"] = hierarchy.as_dict()

    local = created_at.astimezone(ET)
    active_specs = [
        spec
        for spec in config.prediction.horizons
        if spec.trade_eligible
        or spec.role in {"timing", "primary"}
        or local.minute % max(1, int(spec.cadence_minutes)) == 0
    ]
    output = [
        _single_prediction(
            journal,
            config,
            snapshot=snapshot,
            quotes=quotes,
            feature=feature,
            context=context,
            event=event,
            hierarchy=hierarchy,
            spec=spec,
        )
        for spec in active_specs
    ]
    # Consensus is frozen into every member so replay/reporting never depends on
    # querying a newer prediction row.
    primary = [row for row in output if row["payload"]["horizon_role"] in {"timing", "primary"}]
    signs = [1 if row["expected_return"] > 0 else -1 if row["expected_return"] < 0 else 0 for row in primary]
    consensus = {
        "direction_score": float(np.mean(signs)) if signs else 0.0,
        "aligned": bool(signs and abs(float(np.mean(signs))) >= 0.66),
        "members": {
            str(row["payload"]["horizon_name"]): {
                "expected_return": row["expected_return"],
                "probability_up": row["probability_up"],
                "sigma_return": row["sigma_return"],
            }
            for row in primary
        },
    }
    for row in output:
        row["payload"]["multi_horizon_consensus"] = consensus
    return output


def create_prediction(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    feature: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility wrapper returning the trade horizon from the full bundle."""
    bundle = create_prediction_bundle(journal, config, snapshot, feature)
    return next(
        (row for row in bundle if int(row["horizon_minutes"]) == int(config.prediction.horizon_minutes)),
        bundle[0],
    )

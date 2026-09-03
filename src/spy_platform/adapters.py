from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .contracts import AlphaState, BetaState, ModelMeta


def _timestamp(*payloads: dict[str, Any]) -> str:
    for payload in payloads:
        for key in ("timestamp", "created_at", "captured_at", "as_of"):
            value = payload.get(key)
            if value:
                return str(value)
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _quality(value: Any, default: float = 1.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = default
    return max(0.0, min(1.0, out))


def _probability_map(payload: dict[str, Any], *, default_horizon: int = 15) -> dict[int, float]:
    out: dict[int, float] = {}
    horizons = payload.get("horizons") or payload.get("multi_horizon") or payload.get("forecasts")
    if isinstance(horizons, dict):
        for raw_horizon, row in horizons.items():
            try:
                horizon = int(str(raw_horizon).lower().replace("m", ""))
            except ValueError:
                continue
            if isinstance(row, dict):
                value = row.get("probability_up")
            else:
                value = None
            try:
                if value is not None:
                    out[horizon] = float(value)
            except (TypeError, ValueError):
                pass
    value = payload.get("probability_up")
    if value is not None:
        try:
            horizon = int(payload.get("horizon_minutes") or default_horizon)
            out.setdefault(horizon, float(value))
        except (TypeError, ValueError):
            pass
    return out


def _return_map(payload: dict[str, Any], *, default_horizon: int = 15) -> dict[int, float]:
    out: dict[int, float] = {}
    horizons = payload.get("horizons") or payload.get("multi_horizon") or payload.get("forecasts")
    if isinstance(horizons, dict):
        for raw_horizon, row in horizons.items():
            try:
                horizon = int(str(raw_horizon).lower().replace("m", ""))
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            value = row.get("expected_return_bps")
            if value is None and row.get("expected_return") is not None:
                try:
                    value = 10_000.0 * float(row["expected_return"])
                except (TypeError, ValueError):
                    value = None
            try:
                if value is not None:
                    out[horizon] = float(value)
            except (TypeError, ValueError):
                pass
    value = payload.get("expected_return_bps")
    if value is None and payload.get("expected_return") is not None:
        try:
            value = 10_000.0 * float(payload["expected_return"])
        except (TypeError, ValueError):
            value = None
    if value is not None:
        try:
            horizon = int(payload.get("horizon_minutes") or default_horizon)
            out.setdefault(horizon, float(value))
        except (TypeError, ValueError):
            pass
    return out


def alpha_state_from_runtime(
    prediction: dict[str, Any],
    *,
    regime: dict[str, Any] | None = None,
    lifecycle: dict[str, Any] | None = None,
    feature: dict[str, Any] | None = None,
    data_quality: float | None = None,
) -> AlphaState:
    payload = dict(prediction.get("payload") or {})
    combined = {**payload, **prediction}
    model_version = str(
        prediction.get("model_version")
        or payload.get("model_version")
        or payload.get("prediction_model_version")
        or "alpha-legacy-adapter"
    )
    quality = data_quality
    if quality is None:
        quality = prediction.get("data_quality") or payload.get("data_quality") or 1.0
    return AlphaState(
        meta=ModelMeta.create(
            model="ALPHA",
            timestamp=_timestamp(prediction, payload),
            model_version=model_version,
            data_quality=_quality(quality),
        ),
        probability_up=_probability_map(combined),
        expected_return_bps=_return_map(combined),
        expected_mfe_bps=dict(payload.get("expected_mfe_bps") or {}),
        expected_mae_bps=dict(payload.get("expected_mae_bps") or {}),
        regime=dict(regime or payload.get("alpha_regime") or payload.get("regime_state") or {}),
        lifecycle=dict(lifecycle or payload.get("lifecycle") or {}),
        distributions=dict(payload.get("distribution") or payload.get("distributions") or {}),
        uncertainty=dict(payload.get("uncertainty") or {}),
        metrics=dict(feature or {}),
    )


def beta_state_from_runtime(
    state: dict[str, Any],
    *,
    data_quality: float | None = None,
) -> BetaState:
    opportunity = state.get("v2_opportunity") or state.get("opportunity") or state
    if not isinstance(opportunity, dict):
        opportunity = {}
    hgb = opportunity.get("hgb_direction") or {}
    mtf = opportunity.get("mtf_context") or {}
    factors = opportunity.get("factors") or state.get("factors") or {}

    probability_up = _probability_map(mtf)
    expected_return_bps = _return_map(mtf)
    if isinstance(hgb, dict) and hgb.get("probability_up") is not None:
        try:
            probability_up[15] = float(hgb["probability_up"])
        except (TypeError, ValueError):
            pass
        try:
            expected_return_bps[15] = float(hgb.get("expected_return_bps") or 0.0)
        except (TypeError, ValueError):
            pass

    model_version = str(
        (hgb.get("model_version") if isinstance(hgb, dict) else None)
        or opportunity.get("model_version")
        or "beta-legacy-adapter"
    )
    quality = data_quality if data_quality is not None else opportunity.get("data_quality", 1.0)
    breadth = {
        key: value
        for key, value in factors.items()
        if "breadth" in str(key).lower() or "above_vwap" in str(key).lower()
    }
    sectors = {
        key: value for key, value in factors.items() if "sector" in str(key).lower()
    }
    flow = {
        key: value
        for key, value in factors.items()
        if any(token in str(key).lower() for token in ("flow", "aggressor", "imbalance"))
    }
    participation = {
        key: value
        for key, value in factors.items()
        if any(token in str(key).lower() for token in ("participation", "concentration", "leadership"))
    }
    microstructure = {
        key: value
        for key, value in factors.items()
        if any(token in str(key).lower() for token in ("spread", "impact", "absorption", "auction"))
    }
    return BetaState(
        meta=ModelMeta.create(
            model="BETA",
            timestamp=_timestamp(opportunity, state),
            model_version=model_version,
            data_quality=_quality(quality),
        ),
        probability_up=probability_up,
        expected_return_bps=expected_return_bps,
        breadth=breadth,
        sectors=sectors,
        flow=flow,
        participation=participation,
        microstructure=microstructure,
        metrics=dict(factors),
    )

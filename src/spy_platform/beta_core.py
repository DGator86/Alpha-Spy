from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import BetaState, ModelMeta


def _horizon_map(
    forecasts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    field: str,
) -> dict[int, float]:
    output: dict[int, float] = {}
    for forecast in forecasts:
        try:
            horizon = int(forecast.get("horizon_minutes"))
            value = float(forecast.get(field))
        except (TypeError, ValueError):
            continue
        output[horizon] = value
    return output


def _select(factors: dict[str, Any], tokens: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: value
        for key, value in factors.items()
        if any(token in str(key).lower() for token in tokens)
    }


def publish_beta_state(
    *,
    timestamp: datetime | str,
    factors: dict[str, Any],
    forecasts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    sectors: dict[str, Any] | None = None,
    model_version: str,
    data_quality: float,
    source_age_seconds: float = 0.0,
) -> BetaState:
    """Publish the Beta hypothesis as measurements only.

    This is the processor-side boundary for the existing Beta sensor math. It accepts
    Beta's factor/forecast outputs and deliberately has no decision, strategy,
    options-expression, risk, account, or order parameters.
    """
    probability_up = _horizon_map(forecasts, "probability_up")
    expected_return_bps = _horizon_map(forecasts, "expected_return_bps")
    return BetaState(
        meta=ModelMeta.create(
            model="BETA",
            timestamp=timestamp,
            model_version=model_version,
            data_quality=data_quality,
            source_age_seconds=source_age_seconds,
        ),
        probability_up=probability_up,
        expected_return_bps=expected_return_bps,
        breadth=_select(
            factors,
            ("breadth", "pct_above", "pct_ema", "pct_positive", "trend", "momentum"),
        ),
        sectors=dict(sectors or {}),
        flow=_select(
            factors,
            ("flow", "imbalance", "cvd", "initiative", "absorption"),
        ),
        participation=_select(
            factors,
            ("participation", "concentration", "leadership", "coverage"),
        ),
        microstructure=_select(
            factors,
            ("spread", "impact", "auction", "sweep", "acceptance", "poc", "value"),
        ),
        metrics=dict(factors),
    )

from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import AlphaState, ModelMeta


def _numeric_map(values: dict[int | str, Any] | None) -> dict[int, float]:
    output: dict[int, float] = {}
    for key, value in (values or {}).items():
        try:
            horizon = int(str(key).lower().replace("m", ""))
            output[horizon] = float(value)
        except (TypeError, ValueError):
            continue
    return output


def publish_alpha_state(
    *,
    timestamp: datetime | str,
    probability_up: dict[int | str, Any],
    expected_return_bps: dict[int | str, Any],
    model_version: str,
    data_quality: float,
    expected_mfe_bps: dict[int | str, Any] | None = None,
    expected_mae_bps: dict[int | str, Any] | None = None,
    regime: dict[str, Any] | None = None,
    lifecycle: dict[str, Any] | None = None,
    distributions: dict[str, Any] | None = None,
    uncertainty: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    source_age_seconds: float = 0.0,
) -> AlphaState:
    """Publish Alpha statistical measurements with no trade/execution authority."""
    return AlphaState(
        meta=ModelMeta.create(
            model="ALPHA",
            timestamp=timestamp,
            model_version=model_version,
            data_quality=data_quality,
            source_age_seconds=source_age_seconds,
        ),
        probability_up=_numeric_map(probability_up),
        expected_return_bps=_numeric_map(expected_return_bps),
        expected_mfe_bps=_numeric_map(expected_mfe_bps),
        expected_mae_bps=_numeric_map(expected_mae_bps),
        regime=dict(regime or {}),
        lifecycle=dict(lifecycle or {}),
        distributions=dict(distributions or {}),
        uncertainty=dict(uncertainty or {}),
        metrics=dict(metrics or {}),
    )

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ModelName = Literal["ALPHA", "BETA", "GAMMA"]


def _utc_iso(value: datetime | str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ModelMeta:
    model: ModelName
    timestamp: str
    model_version: str
    data_quality: float
    source_age_seconds: float = 0.0
    authority: str = "measurement_only"

    @classmethod
    def create(
        cls,
        *,
        model: ModelName,
        timestamp: datetime | str,
        model_version: str,
        data_quality: float,
        source_age_seconds: float = 0.0,
    ) -> ModelMeta:
        return cls(
            model=model,
            timestamp=_utc_iso(timestamp),
            model_version=model_version,
            data_quality=max(0.0, min(1.0, float(data_quality))),
            source_age_seconds=max(0.0, float(source_age_seconds)),
        )


@dataclass(frozen=True)
class AlphaState:
    meta: ModelMeta
    probability_up: dict[int, float] = field(default_factory=dict)
    expected_return_bps: dict[int, float] = field(default_factory=dict)
    expected_mfe_bps: dict[int, float] = field(default_factory=dict)
    expected_mae_bps: dict[int, float] = field(default_factory=dict)
    regime: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    distributions: dict[str, Any] = field(default_factory=dict)
    uncertainty: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BetaState:
    meta: ModelMeta
    probability_up: dict[int, float] = field(default_factory=dict)
    expected_return_bps: dict[int, float] = field(default_factory=dict)
    breadth: dict[str, Any] = field(default_factory=dict)
    sectors: dict[str, Any] = field(default_factory=dict)
    flow: dict[str, Any] = field(default_factory=dict)
    participation: dict[str, Any] = field(default_factory=dict)
    microstructure: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GammaState:
    meta: ModelMeta
    directional_score: float | None = None
    iv_surface: dict[str, Any] = field(default_factory=dict)
    term_structure: dict[str, Any] = field(default_factory=dict)
    skew: dict[str, Any] = field(default_factory=dict)
    activity: dict[str, Any] = field(default_factory=dict)
    positioning: dict[str, Any] = field(default_factory=dict)
    liquidity: dict[str, Any] = field(default_factory=dict)
    risk_states: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeltaState:
    timestamp: str
    alpha: AlphaState
    beta: BetaState
    gamma: GammaState
    convergence: dict[str, Any]
    conflicts: tuple[str, ...]
    anomalies: tuple[str, ...]
    data_quality: dict[str, Any]
    model_changes: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    authority: str = "compiler_only_no_trade_authority"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

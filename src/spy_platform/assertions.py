from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

QuantRole = Literal[
    "DIRECTION_MOMENTUM",
    "MARKET_INTERNALS",
    "VOLATILITY_DERIVATIVES",
    "STATISTICAL_REGIME",
    "QUANT_SKEPTIC",
]

EconomicRole = Literal[
    "MACRO",
    "RATES_FED",
    "NEWS_CATALYST",
    "INFORMATION_FLOW_SENTIMENT",
]

AssertionRole = QuantRole | EconomicRole

QUANT_STREAM_ACCESS: dict[QuantRole, tuple[str, ...]] = {
    "DIRECTION_MOMENTUM": (
        "direction",
        "path",
        "breadth",
        "options_positioning",
        "divergence",
        "data_quality",
    ),
    "MARKET_INTERNALS": (
        "breadth",
        "flow",
        "direction",
        "liquidity",
        "divergence",
        "data_quality",
    ),
    "VOLATILITY_DERIVATIVES": (
        "volatility",
        "options_positioning",
        "liquidity",
        "direction",
        "divergence",
        "data_quality",
    ),
    "STATISTICAL_REGIME": (
        "direction",
        "regime",
        "path",
        "divergence",
        "data_quality",
    ),
    "QUANT_SKEPTIC": (
        "direction",
        "regime",
        "path",
        "breadth",
        "flow",
        "volatility",
        "options_positioning",
        "liquidity",
        "divergence",
        "anomalies",
        "data_quality",
    ),
}


@dataclass(frozen=True)
class AnalystAssertion:
    assertion_id: str
    timestamp: str
    role: AssertionRole
    thesis: str
    confidence: float
    horizon_minutes: int | None
    evidence: tuple[str, ...]
    contradictions: tuple[str, ...] = ()
    invalidation: tuple[str, ...] = ()
    source_state_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    authority: str = "assertion_only_no_execution_authority"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ManagerView:
    view_id: str
    timestamp: str
    manager: Literal["QUANT_MANAGER", "ECONOMIST"]
    bias: str
    confidence: float
    primary_thesis: str
    primary_risks: tuple[str, ...]
    preferred_condition: str | None
    invalidation: tuple[str, ...]
    assertion_ids: tuple[str, ...]
    authority: str = "synthesis_only_no_execution_authority"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditRecord:
    timestamp: str
    subject_id: str
    subject_type: Literal["MODEL_STATE", "ASSERTION", "MANAGER_VIEW"]
    payload: dict[str, Any]
    outcome_due_at: str | None = None
    immutable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

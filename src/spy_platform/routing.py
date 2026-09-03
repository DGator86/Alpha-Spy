from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .assertions import AnalystAssertion, EconomicRole, QUANT_STREAM_ACCESS, QuantRole

ManagerDestination = Literal["QUANT_MANAGER", "ECONOMIST"]

QUANT_ROLES: frozenset[str] = frozenset(QUANT_STREAM_ACCESS)
ECONOMIC_ROLES: frozenset[str] = frozenset(
    {
        "MACRO",
        "RATES_FED",
        "NEWS_CATALYST",
        "INFORMATION_FLOW_SENTIMENT",
    }
)


@dataclass(frozen=True)
class RoutedAssertion:
    assertion: AnalystAssertion
    destination: ManagerDestination


def route_assertion(assertion: AnalystAssertion) -> RoutedAssertion:
    if assertion.role in QUANT_ROLES:
        return RoutedAssertion(assertion=assertion, destination="QUANT_MANAGER")
    if assertion.role in ECONOMIC_ROLES:
        return RoutedAssertion(assertion=assertion, destination="ECONOMIST")
    raise ValueError(f"unsupported analyst role: {assertion.role}")


def allowed_quant_streams(role: QuantRole) -> tuple[str, ...]:
    return QUANT_STREAM_ACCESS[role]


def validate_quant_stream_request(role: QuantRole, requested: set[str]) -> None:
    allowed = set(allowed_quant_streams(role))
    extra = sorted(requested - allowed)
    if extra:
        raise PermissionError(f"{role} requested unauthorized Delta streams: {', '.join(extra)}")


def is_economic_role(role: str) -> bool:
    return role in ECONOMIC_ROLES


def economic_role(value: str) -> EconomicRole:
    if value not in ECONOMIC_ROLES:
        raise ValueError(f"not an economic role: {value}")
    return value  # type: ignore[return-value]

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from .assertions import AnalystAssertion, EconomicRole, QuantRole
from .bot_specs import analyst_spec
from .routing import route_assertion


@dataclass(frozen=True)
class AnalystWorkPacket:
    packet_id: str
    timestamp: str
    role: QuantRole | EconomicRole
    mission: str
    required_questions: tuple[str, ...]
    inputs: dict[str, Any]
    output_contract: str = "AnalystAssertion"
    execution_authority: bool = False

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ManagerWorkPacket:
    packet_id: str
    timestamp: str
    manager: Literal["QUANT_MANAGER", "ECONOMIST"]
    assertions: tuple[dict[str, Any], ...]
    required_output: str = "ManagerView"
    execution_authority: bool = False

    def as_dict(self):
        return asdict(self)


def quant_work_packet(
    *,
    role: QuantRole,
    packet_id: str,
    timestamp: str,
    delta_streams: dict[str, dict[str, Any]],
) -> AnalystWorkPacket:
    spec = analyst_spec(role)
    inputs = {name: delta_streams[name] for name in spec.inputs if name in delta_streams}
    missing = [name for name in spec.inputs if name not in delta_streams]
    if missing:
        raise RuntimeError(f"missing Delta streams for {role}: {', '.join(missing)}")
    return AnalystWorkPacket(
        packet_id=packet_id,
        timestamp=timestamp,
        role=role,
        mission=spec.mission,
        required_questions=spec.required_questions,
        inputs=inputs,
    )


def economic_work_packet(
    *,
    role: EconomicRole,
    packet_id: str,
    timestamp: str,
    external_inputs: dict[str, Any],
) -> AnalystWorkPacket:
    spec = analyst_spec(role)
    allowed = set(spec.inputs)
    inputs = {key: value for key, value in external_inputs.items() if key in allowed}
    return AnalystWorkPacket(
        packet_id=packet_id,
        timestamp=timestamp,
        role=role,
        mission=spec.mission,
        required_questions=spec.required_questions,
        inputs=inputs,
    )


def manager_work_packet(
    *,
    manager: Literal["QUANT_MANAGER", "ECONOMIST"],
    packet_id: str,
    timestamp: str,
    assertions: list[AnalystAssertion],
) -> ManagerWorkPacket:
    routed = [route_assertion(assertion) for assertion in assertions]
    wrong = [item.assertion.assertion_id for item in routed if item.destination != manager]
    if wrong:
        raise ValueError(f"assertions routed to wrong manager {manager}: {', '.join(wrong)}")
    return ManagerWorkPacket(
        packet_id=packet_id,
        timestamp=timestamp,
        manager=manager,
        assertions=tuple(item.assertion.as_dict() for item in routed),
    )

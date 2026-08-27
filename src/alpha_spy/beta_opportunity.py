from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

BLIND_V1_CONFIG_SHA256 = "ac67ca346e7fd069035e043b3e0e220b780732c01260a09299cdba6bc0a9ed56"


@dataclass(frozen=True)
class BetaOpportunity:
    timestamp: datetime
    eligible: bool
    direction_prior: str
    probability_up: float
    expected_return_bps: float
    supporting_horizons: int
    breadth_5: float | None
    reasons: tuple[str, ...]
    config_sha256: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BetaOpportunity":
        raw_time = str(payload.get("timestamp") or "")
        timestamp = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return cls(
            timestamp=timestamp,
            eligible=bool(payload.get("eligible", False)),
            direction_prior=str(payload.get("direction_prior") or "FLAT"),
            probability_up=float(payload.get("probability_up", 0.5)),
            expected_return_bps=float(payload.get("expected_return_bps", 0.0)),
            supporting_horizons=int(payload.get("supporting_horizons", 0)),
            breadth_5=(
                float(payload["breadth_5"])
                if payload.get("breadth_5") is not None
                else None
            ),
            reasons=tuple(str(item) for item in payload.get("reasons") or ()),
            config_sha256=str(payload.get("config_sha256") or ""),
        )


def opportunity_from_beta_state(state: dict[str, Any]) -> BetaOpportunity | None:
    """Read the handoff from Beta's API state without depending on its option plan.

    The research branch accepts either a top-level `opportunity` key or the
    eventual canonical `snapshot.opportunity` location.  Beta's legacy
    `option_plan` and `decision.structure` are intentionally ignored.
    """

    payload = state.get("opportunity")
    if not isinstance(payload, dict):
        snapshot = state.get("snapshot") or {}
        if isinstance(snapshot, dict):
            payload = snapshot.get("opportunity")
    if not isinstance(payload, dict):
        return None
    try:
        return BetaOpportunity.from_payload(payload)
    except (TypeError, ValueError):
        return None


def opportunity_is_current(
    opportunity: BetaOpportunity | None,
    *,
    as_of: datetime,
    maximum_age_seconds: float = 120.0,
    require_frozen_config: bool = True,
) -> bool:
    if opportunity is None or not opportunity.eligible:
        return False
    if require_frozen_config and opportunity.config_sha256 != BLIND_V1_CONFIG_SHA256:
        return False
    age = (as_of.astimezone(UTC) - opportunity.timestamp.astimezone(UTC)).total_seconds()
    return -5.0 <= age <= maximum_age_seconds


def attach_beta_opportunity(
    prediction: dict[str, Any], opportunity: BetaOpportunity
) -> dict[str, Any]:
    """Attach Beta as probabilistic context, never as a strategy-family command.

    Alpha's P/Q distribution and candidate tournament remain authoritative.
    In particular, `direction_prior` is metadata and MUST NOT be used to reject
    a candidate solely because its payoff points the other way.
    """

    out = deepcopy(prediction)
    payload = dict(out.get("payload") or {})
    payload["beta_opportunity"] = {
        "timestamp": opportunity.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "eligible": opportunity.eligible,
        "direction_prior": opportunity.direction_prior,
        "probability_up": opportunity.probability_up,
        "expected_return_bps": opportunity.expected_return_bps,
        "supporting_horizons": opportunity.supporting_horizons,
        "breadth_5": opportunity.breadth_5,
        "config_sha256": opportunity.config_sha256,
        "strategy_authority": False,
    }
    out["payload"] = payload
    return out

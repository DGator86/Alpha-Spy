from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx


@dataclass(frozen=True)
class BetaV2State:
    timestamp: datetime
    regime: str
    probability_big_move: float
    probability_up_given_big_move: float
    expected_abs_move_bps: float
    validated_direction_edge: float
    magnitude_trust: float
    direction_trust: float
    overall_trust: float
    version: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BetaV2State":
        raw_ts = str(payload.get("timestamp") or "")
        timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return cls(
            timestamp=timestamp.astimezone(UTC),
            regime=str(payload.get("regime") or "UNTRUSTED"),
            probability_big_move=float(payload.get("probability_big_move", 0.5)),
            probability_up_given_big_move=float(payload.get("probability_up_given_big_move", 0.5)),
            expected_abs_move_bps=float(payload.get("expected_abs_move_bps", 0.0)),
            validated_direction_edge=float(payload.get("validated_direction_edge", 0.0)),
            magnitude_trust=float(payload.get("magnitude_trust", 0.0)),
            direction_trust=float(payload.get("direction_trust", 0.0)),
            overall_trust=float(payload.get("overall_trust", 0.0)),
            version=str(payload.get("version") or "beta-spy-v2"),
        )

    def is_current(self, as_of: datetime, maximum_age_seconds: float = 150.0) -> bool:
        now = as_of.astimezone(UTC)
        age = (now - self.timestamp).total_seconds()
        return -5.0 <= age <= maximum_age_seconds

    def as_payload(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "regime": self.regime,
            "probability_big_move": self.probability_big_move,
            "probability_up_given_big_move": self.probability_up_given_big_move,
            "expected_abs_move_bps": self.expected_abs_move_bps,
            "validated_direction_edge": self.validated_direction_edge,
            "magnitude_trust": self.magnitude_trust,
            "direction_trust": self.direction_trust,
            "overall_trust": self.overall_trust,
            "strategy_authority": False,
            "version": self.version,
        }


def parse_beta_v2_state(state: dict[str, Any]) -> BetaV2State | None:
    payload = state.get("v2_state") or state.get("opportunity")
    if not isinstance(payload, dict):
        snapshot = state.get("snapshot") or {}
        if isinstance(snapshot, dict):
            payload = snapshot.get("v2_state")
    if not isinstance(payload, dict):
        return None
    try:
        return BetaV2State.from_payload(payload)
    except (TypeError, ValueError):
        return None


def fetch_beta_v2_state(url: str, *, timeout_seconds: float = 1.5) -> BetaV2State | None:
    if not url:
        return None
    try:
        response = httpx.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return parse_beta_v2_state(payload) if isinstance(payload, dict) else None


def attach_beta_v2_state(prediction: dict[str, Any], state: BetaV2State | None) -> dict[str, Any]:
    out = dict(prediction)
    payload = dict(out.get("payload") or {})
    payload["beta_v2"] = state.as_payload() if state is not None else {
        "regime": "UNAVAILABLE",
        "strategy_authority": False,
        "overall_trust": 0.0,
    }
    out["payload"] = payload
    return out

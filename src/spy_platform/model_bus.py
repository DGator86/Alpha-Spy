from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from .contracts import AlphaState, BetaState, DeltaState, GammaState
from .delta import compile_delta_state

ModelKey = Literal["ALPHA", "BETA", "GAMMA"]


def _stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class BusStatus:
    ready: bool
    reason: str
    timestamps: dict[str, str]
    clock_skew_seconds: float | None


class ModelStateBus:
    """Holds independent model publications and compiles synchronized Delta state.

    Publishing one model never mutates another model's state. Delta compilation is
    refused when a model is missing or timestamps are too far apart.
    """

    def __init__(self, *, max_clock_skew_seconds: float = 90.0) -> None:
        self.max_clock_skew_seconds = float(max_clock_skew_seconds)
        self._alpha: AlphaState | None = None
        self._beta: BetaState | None = None
        self._gamma: GammaState | None = None
        self._previous_delta: DeltaState | None = None

    def publish_alpha(self, state: AlphaState) -> None:
        if state.meta.model != "ALPHA":
            raise ValueError("Alpha publisher received non-Alpha state")
        self._alpha = state

    def publish_beta(self, state: BetaState) -> None:
        if state.meta.model != "BETA":
            raise ValueError("Beta publisher received non-Beta state")
        self._beta = state

    def publish_gamma(self, state: GammaState) -> None:
        if state.meta.model != "GAMMA":
            raise ValueError("Gamma publisher received non-Gamma state")
        self._gamma = state

    def status(self) -> BusStatus:
        states = {
            "ALPHA": self._alpha,
            "BETA": self._beta,
            "GAMMA": self._gamma,
        }
        timestamps = {
            name: state.meta.timestamp
            for name, state in states.items()
            if state is not None
        }
        missing = [name for name, state in states.items() if state is None]
        if missing:
            return BusStatus(
                ready=False,
                reason="missing:" + ",".join(missing),
                timestamps=timestamps,
                clock_skew_seconds=None,
            )
        parsed = [_stamp(state.meta.timestamp) for state in states.values() if state is not None]
        skew = (max(parsed) - min(parsed)).total_seconds()
        if skew > self.max_clock_skew_seconds:
            return BusStatus(
                ready=False,
                reason=f"clock_skew:{skew:.3f}s",
                timestamps=timestamps,
                clock_skew_seconds=skew,
            )
        return BusStatus(
            ready=True,
            reason="synchronized",
            timestamps=timestamps,
            clock_skew_seconds=skew,
        )

    def compile(self) -> DeltaState:
        status = self.status()
        if not status.ready:
            raise RuntimeError(f"Delta compilation refused: {status.reason}")
        assert self._alpha is not None
        assert self._beta is not None
        assert self._gamma is not None
        delta = compile_delta_state(
            self._alpha,
            self._beta,
            self._gamma,
            previous=self._previous_delta,
        )
        self._previous_delta = delta
        return delta

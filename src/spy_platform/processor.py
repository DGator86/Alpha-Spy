from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from alpha_spy.db import Journal

from .adapters import alpha_state_from_runtime, beta_state_from_runtime
from .contracts import DeltaState
from .delta import compile_delta_state
from .gamma import build_gamma_state
from .streams import build_delta_streams


class DeltaProcessor:
    """Read-only bridge from the existing Alpha/Beta runtimes into Delta.

    The processor reads persisted Alpha state, reads Beta's published state over HTTP,
    derives Gamma from archived option chains, and compiles a Delta snapshot.  It never
    imports Alpha execution/risk modules and has no broker client.
    """

    def __init__(
        self,
        journal: Journal,
        *,
        beta_state_url: str = "http://127.0.0.1:8790/api/state",
        beta_timeout_seconds: float = 2.5,
    ) -> None:
        self.journal = journal
        self.beta_state_url = beta_state_url
        self.beta_timeout_seconds = beta_timeout_seconds
        self._previous: DeltaState | None = None

    def _alpha_prediction(self) -> tuple[dict[str, Any], dict[str, Any]]:
        predictions = self.journal.latest_predictions_by_horizon()
        base = predictions.get("15m") or self.journal.latest_prediction()
        if not base:
            raise RuntimeError("alpha_prediction_unavailable")
        combined = dict(base)
        payload = dict(combined.get("payload") or {})
        horizon_rows: dict[str, Any] = {}
        for name, row in predictions.items():
            horizon_rows[name] = {
                "probability_up": row.get("probability_up"),
                "expected_return": row.get("expected_return"),
                "expected_return_bps": (
                    10_000.0 * float(row["expected_return"])
                    if row.get("expected_return") is not None
                    else None
                ),
                "horizon_minutes": row.get("horizon_minutes"),
                "created_at": row.get("created_at"),
            }
        payload["horizons"] = horizon_rows
        combined["payload"] = payload
        return combined, payload

    def _beta_state(self) -> dict[str, Any]:
        response = httpx.get(self.beta_state_url, timeout=self.beta_timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("beta_state_invalid")
        return payload

    def _gamma_chains(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for purpose in ("strategy", "iv_reference"):
            chain, options = self.journal.latest_option_chain("SPY", purpose)
            if not chain or not options:
                continue
            key = f"{chain.get('expiration')}|{purpose}"
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "expiration": chain.get("expiration"),
                    "captured_at": chain.get("captured_at"),
                    "purpose": purpose,
                    "options": options,
                }
            )
        return output

    @staticmethod
    def _alpha_quality(feature: dict[str, Any]) -> float:
        state = str(feature.get("health_state") or "").upper()
        if state == "GREEN":
            return 1.0
        if state in {"YELLOW", "ORANGE"}:
            return 0.75
        if state == "RED":
            return 0.35
        return 0.85

    def build(self) -> DeltaState:
        snapshot = self.journal.latest_snapshot()
        feature = self.journal.latest_features() or {}
        if not snapshot:
            raise RuntimeError("alpha_snapshot_unavailable")
        prediction, payload = self._alpha_prediction()
        beta_payload = self._beta_state()
        alpha = alpha_state_from_runtime(
            prediction,
            regime=(payload.get("regime_hierarchy") or payload.get("regime_state") or {}),
            lifecycle=(payload.get("lifecycle") or {}),
            feature=feature,
            data_quality=self._alpha_quality(feature),
        )
        beta = beta_state_from_runtime(beta_payload)
        gamma = build_gamma_state(
            timestamp=str(snapshot.get("captured_at") or datetime.now(UTC).isoformat()),
            spot=float(snapshot.get("spy_price") or 0.0),
            chains=self._gamma_chains(),
        )
        delta = compile_delta_state(alpha, beta, gamma, previous=self._previous)
        self._previous = delta
        return delta

    def streams(self) -> dict[str, dict[str, Any]]:
        return build_delta_streams(self.build())

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

import numpy as np

from .config import ForecastHorizonSpec, SuiteConfig
from .db import Journal
from .events import EventState
from .prediction import canonical_hash, create_prediction_for_horizon
from .timeutil import utc_iso, utc_now

_NUMERIC_FIELDS = (
    "spy_price",
    "predicted_price",
    "predicted_low",
    "predicted_high",
    "probability_up",
    "expected_return",
    "sigma_return",
)


def _event_from_prediction(prediction: dict[str, Any]) -> EventState:
    raw = prediction.get("payload", {}).get("event_state", {})
    if not isinstance(raw, dict):
        raw = {}
    return EventState(
        state=str(raw.get("state") or "unknown"),
        source=str(raw.get("source") or "frozen_prediction"),
        title=raw.get("title"),
        starts_at=raw.get("starts_at"),
        ends_at=raw.get("ends_at"),
        minutes_to_event=raw.get("minutes_to_event"),
        blocked=bool(raw.get("blocked", False)),
    )


def _spec(config: SuiteConfig, prediction: dict[str, Any]) -> ForecastHorizonSpec:
    name = str(prediction.get("payload", {}).get("horizon_name") or "")
    for item in config.prediction.horizons:
        if item.name == name:
            return item
    minutes = int(prediction.get("horizon_minutes") or 0)
    for item in config.prediction.horizons:
        if item.minutes == minutes:
            return item
    return ForecastHorizonSpec(name=name or f"{minutes}m", minutes=max(1, minutes), role="research")


def _select_rows(rows: list[dict[str, Any]], samples: int) -> list[dict[str, Any]]:
    if len(rows) <= samples:
        return rows
    # Deterministic coverage over the whole captured period, not a random cherry-pick.
    indices = np.linspace(0, len(rows) - 1, samples, dtype=int)
    return [rows[int(index)] for index in indices]


def _close(a: Any, b: Any, *, atol: float = 1e-10, rtol: float = 1e-8) -> bool:
    try:
        return bool(np.isclose(float(a), float(b), atol=atol, rtol=rtol, equal_nan=True))
    except (TypeError, ValueError):
        return a == b


class ReplayVerifier:
    """Recompute frozen forecasts from Alpha-SPY's captured production tape.

    This is deliberately an *as-of* replay.  It never asks Tradier for historical
    state during verification and therefore cannot accidentally introduce revised
    or future data.  The external event/calendar state is frozen from the original
    prediction; market context, covariance, calibration and P/Q distributions are
    recomputed from database rows timestamped at or before the original snapshot.
    """

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal

    def _eligible_predictions(self) -> list[dict[str, Any]]:
        with self.journal.session() as con:
            rows = con.execute(
                """
                SELECT p.*
                FROM predictions p
                JOIN prediction_outcomes o ON o.prediction_id=p.prediction_id
                WHERE p.model_version=?
                  AND p.config_hash=?
                  AND o.integrity IN ('VERIFIED','MINOR_REVISION')
                ORDER BY p.created_at ASC
                """,
                (self.config.prediction.model_version, self.config.decision_fingerprint()),
            ).fetchall()
        output: list[dict[str, Any]] = []
        import json

        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return output

    def run(self, samples: int | None = None) -> dict[str, Any]:
        requested = int(samples or self.config.validation.replay_sample_predictions)
        rows = _select_rows(self._eligible_predictions(), max(1, requested))
        mismatch_rows: list[dict[str, Any]] = []
        checked: list[dict[str, Any]] = []

        for original in rows:
            snapshot = self.journal.snapshot(str(original["snapshot_id"]))
            feature = self.journal.features_for_snapshot(str(original["snapshot_id"]))
            if snapshot is None or feature is None:
                mismatch_rows.append(
                    {"prediction_id": original["prediction_id"], "reason": "missing_frozen_input"}
                )
                continue
            feature_fingerprint = {k: v for k, v in feature.items() if k not in {"payload", "created_at"}}
            actual_feature_hash = canonical_hash(feature_fingerprint)
            if actual_feature_hash != str(original.get("feature_hash") or ""):
                mismatch_rows.append(
                    {
                        "prediction_id": original["prediction_id"],
                        "reason": "feature_hash_changed",
                        "stored": original.get("feature_hash"),
                        "current": actual_feature_hash,
                    }
                )
                continue

            payload = original.get("payload", {})
            target_at = datetime.fromisoformat(str(original["target_at"]).replace("Z", "+00:00"))
            try:
                replayed = create_prediction_for_horizon(
                    self.journal,
                    self.config,
                    snapshot,
                    feature,
                    _spec(self.config, original),
                    quotes=self.journal.snapshot_quotes(str(snapshot["snapshot_id"])),
                    frozen_event=_event_from_prediction(original),
                    frozen_target_at=target_at,
                    frozen_calendar_meta=payload.get("calendar", {}),
                )
            except Exception as exc:  # replay must report a failed sample, never hide it
                mismatch_rows.append(
                    {
                        "prediction_id": original["prediction_id"],
                        "reason": "recompute_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            differences: dict[str, Any] = {}
            for field in _NUMERIC_FIELDS:
                if not _close(original.get(field), replayed.get(field)):
                    differences[field] = {
                        "stored": original.get(field),
                        "replayed": replayed.get(field),
                    }
            for field in ("config_hash", "feature_hash", "regime", "model_version"):
                if str(original.get(field)) != str(replayed.get(field)):
                    differences[field] = {
                        "stored": original.get(field),
                        "replayed": replayed.get(field),
                    }
            replay_payload = replayed.get("payload", {})
            for component in ("signal_model", "shadow_model", "path", "market_context"):
                stored_component = payload.get(component)
                replay_component = replay_payload.get(component)
                if canonical_hash(stored_component) != canonical_hash(replay_component):
                    differences[f"payload.{component}_hash"] = {
                        "stored": canonical_hash(stored_component),
                        "replayed": canonical_hash(replay_component),
                    }
            stored_dist = payload.get("distribution", {})
            replay_dist = replay_payload.get("distribution", {})
            for field in (
                "p_source",
                "q_source",
                "covariance_symbols",
                "covariance_observations",
                "q_surface_symbols",
            ):
                if stored_dist.get(field) != replay_dist.get(field):
                    differences[f"distribution.{field}"] = {
                        "stored": stored_dist.get(field),
                        "replayed": replay_dist.get(field),
                    }
            for field in (
                "p_expected_return",
                "p_volatility",
                "q_expected_return",
                "q_volatility",
                "q_surface_covered_weight",
                "correlation_risk_premium",
                "model_uncertainty",
            ):
                if not _close(stored_dist.get(field), replay_dist.get(field), atol=1e-9, rtol=1e-7):
                    differences[f"distribution.{field}"] = {
                        "stored": stored_dist.get(field),
                        "replayed": replay_dist.get(field),
                    }
            # Moments can match while tails/skew differ.  Promotion replay therefore
            # verifies the entire persisted P/Q probability grid used by the strategy
            # ranker, not only summary statistics.
            for field in ("probability_grid", "p_price_quantiles", "q_price_quantiles"):
                stored_shape = stored_dist.get(field)
                replay_shape = replay_dist.get(field)
                if canonical_hash(stored_shape) != canonical_hash(replay_shape):
                    differences[f"distribution.{field}_hash"] = {
                        "stored": canonical_hash(stored_shape),
                        "replayed": canonical_hash(replay_shape),
                    }
            if differences:
                mismatch_rows.append(
                    {
                        "prediction_id": original["prediction_id"],
                        "created_at": original["created_at"],
                        "horizon": payload.get("horizon_name"),
                        "reason": "deterministic_mismatch",
                        "differences": differences,
                    }
                )
            checked.append(
                {
                    "prediction_id": original["prediction_id"],
                    "created_at": original["created_at"],
                    "horizon": payload.get("horizon_name"),
                    "config_hash": original.get("config_hash"),
                    "replay_hash": hashlib.sha256(
                        repr({field: replayed.get(field) for field in _NUMERIC_FIELDS}).encode()
                    ).hexdigest(),
                }
            )

        created_at = utc_iso(utc_now())
        mismatch_count = len(mismatch_rows)
        status = "PASSED" if rows and mismatch_count == 0 and len(checked) == len(rows) else "FAILED"
        row = {
            "replay_id": f"R-{hashlib.sha256((created_at + self.config.prediction.model_version).encode()).hexdigest()[:16]}",
            "created_at": created_at,
            "start_at": rows[0]["created_at"] if rows else None,
            "end_at": rows[-1]["created_at"] if rows else None,
            "samples": len(rows),
            "mismatches": mismatch_count,
            "status": status,
            "payload": {
                "model_version": self.config.prediction.model_version,
                "requested_samples": requested,
                "checked_samples": len(checked),
                "mismatches": mismatch_rows,
                "sample_manifest": checked,
                "method": "captured_tape_asof_recompute_v2_full_distribution",
            },
        }
        self.journal.insert_replay_run(row)
        return row

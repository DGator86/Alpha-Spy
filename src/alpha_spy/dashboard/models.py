from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SnapshotEnvelope(BaseModel):
    timestamp: str = Field(default_factory=utc_now_iso)
    payload: dict[str, Any]


class PredictionRecord(BaseModel):
    prediction_id: str
    created_at: str
    target_at: str
    horizon_minutes: int = 15
    spy_price: float
    predicted_price: float
    predicted_low: float
    predicted_high: float
    probability_up: float = Field(ge=0.0, le=1.0)
    actual_price: float | None = None
    direction_correct: bool | None = None
    integrity: Literal[
        "PENDING", "VERIFIED", "MINOR_REVISION", "MATERIAL_REVISION", "INCOMPLETE", "INVALID"
    ] = "PENDING"
    model_version: str = "unknown"
    payload: dict[str, Any] = Field(default_factory=dict)


class OutcomeRecord(BaseModel):
    prediction_id: str
    confirmed_at: str = Field(default_factory=utc_now_iso)
    actual_price: float
    actual_high: float | None = None
    actual_low: float | None = None
    actual_return: float | None = None
    actual_pnl: float | None = None
    actual_mfe: float | None = None
    actual_mae: float | None = None
    integrity: Literal[
        "VERIFIED", "MINOR_REVISION", "MATERIAL_REVISION", "INCOMPLETE", "INVALID"
    ] = "VERIFIED"
    payload: dict[str, Any] = Field(default_factory=dict)


class AlertRecord(BaseModel):
    severity: Literal["info", "warning", "critical"]
    title: str
    message: str
    source: str = "system"
    timestamp: str = Field(default_factory=utc_now_iso)


class CommandRequest(BaseModel):
    command: Literal["PAUSE_NEW_ENTRIES", "RESUME_NEW_ENTRIES", "FLATTEN_MANAGED_POSITION", "RELOAD_MODEL"]
    confirm: str = ""
    reason: str = "operator"


class CommandStatus(BaseModel):
    command_id: int
    status: Literal["pending", "acknowledged", "completed", "failed"]
    message: str = ""

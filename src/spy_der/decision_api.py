from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

from .config import load_config
from .db import Journal
from .state import build_dashboard_state
from .timeutil import utc_iso

CONFIG_PATH = os.getenv("SPY_DER_CONFIG", "/etc/spy-der/config.yaml")
config = load_config(CONFIG_PATH)
journal = Journal(config.paths.database)
app = FastAPI(title="SPY Constituent Alpha Decision Service", version="2.0.0")


def _authorize(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = config.dashboard.admin_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin token is not configured")
    supplied = authorization or ""
    if supplied.startswith("Bearer "):
        supplied = supplied[7:]
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": "2.0.0",
        "database": str(config.paths.database),
        "quick_check": journal.integrity_check(),
        "timestamp": utc_iso(),
    }


@app.get("/api/v1/state")
def state() -> dict:
    return build_dashboard_state(config, journal)


@app.post("/api/v1/control/pause")
def pause(authorization: Annotated[str | None, Header()] = None) -> dict:
    _authorize(authorization)
    journal.set_control("entries_paused", "true")
    return {"applied": True, "entries_paused": True}


@app.post("/api/v1/control/resume")
def resume(authorization: Annotated[str | None, Header()] = None) -> dict:
    _authorize(authorization)
    journal.set_control("entries_paused", "false")
    return {"applied": True, "entries_paused": False}


@app.post("/api/v1/control/flatten")
def flatten(
    confirmation: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    _authorize(authorization)
    if confirmation != "FLATTEN_SPY_ALPHA_POSITION":
        raise HTTPException(status_code=400, detail="Confirmation phrase is incorrect")
    journal.set_control("flatten_requested", "true")
    return {"queued": True}


@app.post("/api/v1/control/reload-model")
def reload_model(authorization: Annotated[str | None, Header()] = None) -> dict:
    _authorize(authorization)
    journal.set_control("reload_model_requested", utc_iso())
    return {"queued": True}

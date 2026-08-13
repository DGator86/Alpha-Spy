from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .db import Repository
from .demo import demo_loop
from .models import (
    AlertRecord,
    CommandRequest,
    CommandStatus,
    OutcomeRecord,
    PredictionRecord,
    SnapshotEnvelope,
    utc_now_iso,
)
from .security import verify_admin_token, verify_ingest_token, verify_view_token, websocket_authorized
from .service import DashboardService, SectionStream


class Runtime:
    settings: Settings
    repo: Repository
    service: DashboardService
    demo_task: asyncio.Task | None = None


runtime = Runtime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime.settings = get_settings()
    runtime.repo = Repository(runtime.settings.dashboard_db)
    runtime.service = DashboardService(runtime.settings, runtime.repo)
    await runtime.service.start()
    if runtime.settings.dashboard_mode == "demo":
        runtime.demo_task = asyncio.create_task(demo_loop(runtime.repo), name="dashboard-demo")
    try:
        yield
    finally:
        if runtime.demo_task:
            runtime.demo_task.cancel()
            await asyncio.gather(runtime.demo_task, return_exceptions=True)
        await runtime.service.stop()


app = FastAPI(title="SPY Alpha Command Center", version="3.0.0", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def view_guard(
    authorization: Annotated[str | None, Header()] = None,
    x_dashboard_token: Annotated[str | None, Header()] = None,
) -> None:
    verify_view_token(runtime.settings, authorization, x_dashboard_token)


def admin_guard(
    authorization: Annotated[str | None, Header()] = None,
    x_dashboard_token: Annotated[str | None, Header()] = None,
) -> None:
    verify_admin_token(runtime.settings, authorization, x_dashboard_token)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/v1/auth/mode")
async def auth_mode() -> dict:
    return {
        "view_token_required": runtime.settings.dashboard_require_view_token,
        "admin_token_configured": bool(runtime.settings.dashboard_admin_token),
        "mode": runtime.settings.dashboard_mode,
    }


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok", "version": "3.0.0", "mode": runtime.settings.dashboard_mode, "timestamp": utc_now_iso()}


@app.get("/api/v1/dashboard/state", dependencies=[Depends(view_guard)])
async def dashboard_state() -> dict:
    return runtime.service.build_state()


@app.get("/api/v1/predictions", dependencies=[Depends(view_guard)])
async def predictions(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    return runtime.repo.list_predictions(limit)


@app.get("/api/v1/alerts", dependencies=[Depends(view_guard)])
async def alerts(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    return runtime.repo.list_alerts(limit)


@app.get("/api/v1/predictions/{prediction_id}", dependencies=[Depends(view_guard)])
async def prediction_detail(prediction_id: str) -> dict:
    """Full forecast record including the model payload, for the inspector."""
    record = runtime.repo.get_prediction(prediction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    return record


@app.post("/api/v1/ingest/snapshot")
async def ingest_snapshot(
    envelope: SnapshotEnvelope,
    x_ingest_token: Annotated[str | None, Header()] = None,
) -> dict:
    verify_ingest_token(runtime.settings, x_ingest_token)
    runtime.repo.set_state("live", envelope.payload, envelope.timestamp)
    return {"accepted": True, "timestamp": envelope.timestamp}


@app.post("/api/v1/ingest/prediction")
async def ingest_prediction(
    record: PredictionRecord,
    x_ingest_token: Annotated[str | None, Header()] = None,
) -> dict:
    verify_ingest_token(runtime.settings, x_ingest_token)
    runtime.repo.upsert_prediction(record.model_dump())
    return {"accepted": True, "prediction_id": record.prediction_id}


@app.post("/api/v1/ingest/outcome")
async def ingest_outcome(
    record: OutcomeRecord,
    x_ingest_token: Annotated[str | None, Header()] = None,
) -> dict:
    verify_ingest_token(runtime.settings, x_ingest_token)
    if not runtime.repo.apply_outcome(record.model_dump()):
        raise HTTPException(status_code=404, detail="Prediction not found")
    return {"accepted": True, "prediction_id": record.prediction_id}


@app.post("/api/v1/ingest/alert")
async def ingest_alert(
    record: AlertRecord,
    x_ingest_token: Annotated[str | None, Header()] = None,
) -> dict:
    verify_ingest_token(runtime.settings, x_ingest_token)
    alert_id = runtime.repo.add_alert(record.model_dump())
    return {"accepted": True, "alert_id": alert_id}


@app.post("/api/v1/control/command", dependencies=[Depends(admin_guard)])
async def control_command(record: CommandRequest) -> dict:
    if record.command == "FLATTEN_MANAGED_POSITION" and record.confirm != "FLATTEN_SPY_ALPHA_POSITION":
        raise HTTPException(status_code=400, detail="Flatten confirmation phrase is incorrect")
    command_id = runtime.repo.queue_command(utc_now_iso(), record.command, record.reason)
    return {"queued": True, "command_id": command_id, "command": record.command}


@app.get("/api/v1/control/commands/pending")
async def pending_commands(x_ingest_token: Annotated[str | None, Header()] = None) -> list[dict]:
    verify_ingest_token(runtime.settings, x_ingest_token)
    return runtime.repo.list_commands(100, pending_only=True)


@app.post("/api/v1/control/commands/{command_id}/status")
async def update_command_status(
    command_id: int,
    record: CommandStatus,
    x_ingest_token: Annotated[str | None, Header()] = None,
) -> dict:
    verify_ingest_token(runtime.settings, x_ingest_token)
    if command_id != record.command_id:
        raise HTTPException(status_code=400, detail="Command id mismatch")
    done = record.status in {"completed", "failed"}
    if not runtime.repo.update_command(command_id, record.status, record.message, utc_now_iso() if done else None):
        raise HTTPException(status_code=404, detail="Command not found")
    return {"updated": True}


@app.post("/api/v1/alerts/{alert_id}/acknowledge", dependencies=[Depends(admin_guard)])
async def acknowledge_alert(alert_id: int) -> dict:
    if not runtime.repo.acknowledge_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"acknowledged": True, "alert_id": alert_id}


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """Stream the dashboard state as an opening snapshot followed by deltas.

    The first frame is ``{"type": "snapshot", "sections": {...}}`` carrying every
    section. Subsequent frames are ``{"type": "patch", "sections": {...}}`` and
    contain only the sections whose contents actually changed, so a SPY tick no
    longer drags 120 predictions, 60 alerts and the promotion evidence across the
    wire behind it. A ``heartbeat`` frame is sent when nothing changed, which
    keeps the connection warm and gives the client a liveness signal it can
    distinguish from a stalled engine.
    """
    if not await websocket_authorized(websocket, runtime.settings):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    stream = SectionStream()
    try:
        await websocket.send_json(stream.snapshot(runtime.service.build_state()))
        while True:
            await asyncio.sleep(runtime.settings.websocket_interval_seconds)
            frame = stream.patch(runtime.service.build_state())
            if frame is None:
                frame = {"type": "heartbeat", "seq": stream.seq, "timestamp": utc_now_iso()}
            await websocket.send_json(frame)
    except WebSocketDisconnect:
        pass


# Registered last so it can never shadow an API or socket route. The workstation
# routes client-side, so a refresh on /governance/validation has to return the
# shell rather than a 404.
@app.get("/{path:path}")
async def spa_fallback(path: str) -> FileResponse:
    if path.startswith(("api/", "ws/", "static/")):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(static_dir / "index.html")

from __future__ import annotations

import secrets

from fastapi import HTTPException, WebSocket

from .config import Settings


def _extract_token(authorization: str | None, x_dashboard_token: str | None) -> str:
    if x_dashboard_token:
        return x_dashboard_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def verify_view_token(
    settings: Settings,
    authorization: str | None,
    x_dashboard_token: str | None,
) -> None:
    if not settings.dashboard_require_view_token:
        return
    supplied = _extract_token(authorization, x_dashboard_token)
    expected = settings.dashboard_view_token
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid dashboard view token required")


def verify_admin_token(
    settings: Settings,
    authorization: str | None,
    x_dashboard_token: str | None,
) -> None:
    supplied = _extract_token(authorization, x_dashboard_token)
    expected = settings.dashboard_admin_token
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Valid dashboard administrator token required")


def verify_ingest_token(settings: Settings, supplied: str | None) -> None:
    expected = settings.dashboard_ingest_token
    if not expected or not supplied or not secrets.compare_digest(supplied.strip(), expected):
        raise HTTPException(status_code=403, detail="Valid ingestion token required")


async def websocket_authorized(websocket: WebSocket, settings: Settings) -> bool:
    if not settings.dashboard_require_view_token:
        return True
    supplied = websocket.query_params.get("token", "")
    expected = settings.dashboard_view_token
    return bool(expected and supplied and secrets.compare_digest(supplied, expected))

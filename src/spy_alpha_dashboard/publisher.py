from __future__ import annotations

from typing import Any
import httpx


class DashboardPublisher:
    """Small engine-side client for publishing live state and audit records."""

    def __init__(self, base_url: str, ingest_token: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.ingest_token = ingest_token
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Ingest-Token": self.ingest_token}

    def publish_snapshot(self, payload: dict[str, Any], timestamp: str | None = None) -> None:
        body: dict[str, Any] = {"payload": payload}
        if timestamp:
            body["timestamp"] = timestamp
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/api/v1/ingest/snapshot", json=body, headers=self.headers)
            response.raise_for_status()

    def publish_prediction(self, record: dict[str, Any]) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/api/v1/ingest/prediction", json=record, headers=self.headers)
            response.raise_for_status()

    def publish_outcome(self, record: dict[str, Any]) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/api/v1/ingest/outcome", json=record, headers=self.headers)
            response.raise_for_status()

    def publish_alert(self, record: dict[str, Any]) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/api/v1/ingest/alert", json=record, headers=self.headers)
            response.raise_for_status()

from __future__ import annotations

from typing import Any

import httpx

from .config import SuiteConfig


class DashboardPublisher:
    def __init__(self, config: SuiteConfig):
        self.base_url = f"http://{config.dashboard.host}:{config.dashboard.port}"
        self.token = config.dashboard.ingest_token.get_secret_value()
        self.timeout = 4.0

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _post(self, path: str, body: dict[str, Any]) -> None:
        if not self.configured:
            return
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}{path}",
                json=body,
                headers={"X-Ingest-Token": self.token},
            )
            response.raise_for_status()

    def snapshot(self, payload: dict[str, Any], timestamp: str) -> None:
        self._post("/api/v1/ingest/snapshot", {"timestamp": timestamp, "payload": payload})

    def prediction(self, record: dict[str, Any]) -> None:
        self._post("/api/v1/ingest/prediction", record)

    def outcome(self, record: dict[str, Any]) -> None:
        self._post("/api/v1/ingest/outcome", record)

    def alert(self, severity: str, title: str, message: str, source: str = "engine") -> None:
        self._post(
            "/api/v1/ingest/alert",
            {"severity": severity, "title": title, "message": message, "source": source},
        )

    def pending_commands(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/api/v1/control/commands/pending",
                headers={"X-Ingest-Token": self.token},
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else []

    def command_status(self, command_id: int, status: str, message: str = "") -> None:
        self._post(
            f"/api/v1/control/commands/{command_id}/status",
            {"command_id": command_id, "status": status, "message": message},
        )

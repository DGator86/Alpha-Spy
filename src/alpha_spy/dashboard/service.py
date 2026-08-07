from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .db import Repository
from .tradier import TradierReadOnlyClient


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DashboardService:
    def __init__(self, settings: Settings, repo: Repository):
        self.settings = settings
        self.repo = repo
        self.tradier: TradierReadOnlyClient | None = None
        self.tradier_quote: dict[str, Any] | None = None
        self.tradier_account: dict[str, Any] | None = None
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        if self.settings.tradier_access_token:
            self.tradier = TradierReadOnlyClient(self.settings)
            self._tasks.append(asyncio.create_task(self._poll_tradier_account(), name="tradier-account-poll"))
            if self.settings.tradier_stream_enabled:
                self._tasks.append(asyncio.create_task(self._stream_tradier(), name="tradier-market-stream"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self.tradier:
            await self.tradier.close()

    async def _poll_tradier_account(self) -> None:
        assert self.tradier is not None
        while True:
            try:
                balances, positions, orders = await asyncio.gather(
                    self.tradier.balances(), self.tradier.positions(), self.tradier.orders()
                )
                self.tradier_account = {
                    "updated_at": utc_now_iso(),
                    "balances": balances,
                    "positions": positions,
                    "orders": orders,
                }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.tradier_account = {"updated_at": utc_now_iso(), "error": str(exc)}
            await asyncio.sleep(10.0)

    async def _stream_tradier(self) -> None:
        assert self.tradier is not None
        async for event in self.tradier.market_stream(["SPY"]):
            self.tradier_quote = {"updated_at": utc_now_iso(), "event": event}

    def build_state(self) -> dict[str, Any]:
        state = self.repo.get_state("live") or {
            "timestamp": utc_now_iso(),
            "engine": {"name": self.settings.engine_name, "version": self.settings.engine_version, "environment": self.settings.tradier_env.upper(), "mode": "WAITING"},
            "health": {"state": "RED", "trust_score": 0.0, "components": {}},
            "account": {}, "market": {}, "position": {"open": False},
            "audit": {}, "strategy_matrix": [], "challengers": [], "services": [],
            "price_series": [], "prediction_series": [], "attribution": [],
        }
        state = dict(state)
        state["predictions"] = self.repo.list_predictions(120)
        state["prediction_metrics"] = self.repo.prediction_metrics(500)
        state["alerts"] = self.repo.list_alerts(60)
        state["commands"] = self.repo.list_commands(30)
        state["tradier"] = {
            "configured": bool(self.settings.tradier_access_token),
            "environment": self.settings.tradier_env,
            "quote": self.tradier_quote,
            "account": self.tradier_account,
        }
        return state

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets

from .config import Settings

log = logging.getLogger(__name__)


class TradierReadOnlyClient:
    """Read-only Tradier client used only for account/quote visibility.

    Trading remains the responsibility of the execution engine. No order-submission
    methods are intentionally exposed here.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.tradier_base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {settings.tradier_access_token}",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.settings.tradier_access_token:
            raise RuntimeError("TRADIER_ACCESS_TOKEN is not configured")
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def profile(self) -> dict[str, Any]:
        return await self._get("/user/profile")

    async def balances(self) -> dict[str, Any]:
        account = self.settings.tradier_account_id
        if not account:
            raise RuntimeError("TRADIER_ACCOUNT_ID is not configured")
        return await self._get(f"/accounts/{account}/balances")

    async def positions(self) -> dict[str, Any]:
        account = self.settings.tradier_account_id
        if not account:
            raise RuntimeError("TRADIER_ACCOUNT_ID is not configured")
        return await self._get(f"/accounts/{account}/positions")

    async def orders(self) -> dict[str, Any]:
        account = self.settings.tradier_account_id
        if not account:
            raise RuntimeError("TRADIER_ACCOUNT_ID is not configured")
        return await self._get(f"/accounts/{account}/orders")

    async def quotes(self, symbols: list[str], greeks: bool = False) -> dict[str, Any]:
        return await self._get(
            "/markets/quotes",
            params={"symbols": ",".join(symbols), "greeks": str(greeks).lower()},
        )

    async def create_market_session(self) -> str:
        if not self.settings.tradier_access_token:
            raise RuntimeError("TRADIER_ACCESS_TOKEN is not configured")
        response = await self._client.post("/markets/events/session")
        response.raise_for_status()
        payload = response.json()
        session_id = payload.get("stream", {}).get("sessionid") or payload.get("sessionid")
        if not session_id:
            raise RuntimeError("Tradier did not return a market streaming session id")
        return str(session_id)

    async def market_stream(self, symbols: list[str]) -> AsyncIterator[dict[str, Any]]:
        """Yield quote/trade/timesale events with reconnect and backoff."""
        if self.settings.tradier_env == "sandbox":
            endpoint = "wss://sandbox-ws.tradier.com/v1/markets/events"
        else:
            endpoint = "wss://ws.tradier.com/v1/markets/events"
        delay = 1.0
        while True:
            try:
                session_id = await self.create_market_session()
                async with websockets.connect(endpoint, compression=None, ping_interval=20, ping_timeout=20) as ws:
                    request = {
                        "symbols": symbols,
                        "filter": ["quote", "trade", "timesale", "summary"],
                        "sessionid": session_id,
                        "linebreak": True,
                        "validOnly": True,
                    }
                    await ws.send(json.dumps(request))
                    delay = 1.0
                    async for raw in ws:
                        for line in str(raw).splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                yield json.loads(line)
                            except json.JSONDecodeError:
                                log.warning("Unparseable Tradier stream payload")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Tradier stream disconnected: %s", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 30.0)

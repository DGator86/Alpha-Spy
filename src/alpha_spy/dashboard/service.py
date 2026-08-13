from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .db import Repository
from .tradier import TradierReadOnlyClient


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# Section names are the unit of change on the wire. The engine republishes the
# whole snapshot every cycle, but the parts of it move at wildly different
# cadences: a SPY tick changes several times a second while the validation gate
# list changes once a day. Splitting the state lets the socket send the tick
# without resending 5,000 forecasts' worth of promotion evidence behind it.
SECTIONS: dict[str, tuple[str, ...]] = {
    "engine": ("engine",),
    "session": ("session",),
    "market": ("market",),
    "forecast": ("forecast_horizons", "prediction_series", "price_series"),
    "decision": ("decision",),
    "candidates": ("candidates",),
    "position": ("position", "broker_reconciliation"),
    "account": ("account",),
    "health": ("health",),
    "audit": ("audit", "prediction_metrics"),
    "predictions": ("predictions",),
    "alerts": ("alerts",),
    "commands": ("commands",),
    "validation": ("promotion", "replay"),
    "security": ("security",),
    "services": ("services", "tradier"),
    "research": (
        "strategy_matrix",
        "challengers",
        "attribution",
        "constituent_attribution",
    ),
}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.blake2b(encoded, digest_size=8).hexdigest()


def split_sections(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Split a flat dashboard state into the sections published on the socket."""
    sections: dict[str, dict[str, Any]] = {}
    for name, keys in SECTIONS.items():
        section = {key: state[key] for key in keys if key in state}
        if section:
            sections[name] = section
    return sections


class SectionStream:
    """Tracks what a single socket has already been sent.

    One instance per connection, so a client that joins mid-session still gets a
    complete opening snapshot while established clients keep receiving deltas.
    """

    def __init__(self) -> None:
        self.seq = 0
        self._digests: dict[str, str] = {}

    def snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        sections = split_sections(state)
        self._digests = {name: _digest(body) for name, body in sections.items()}
        self.seq += 1
        return {
            "type": "snapshot",
            "seq": self.seq,
            "timestamp": state.get("timestamp") or utc_now_iso(),
            "sections": sections,
        }

    def patch(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Return a frame carrying only the sections that changed, or None."""
        sections = split_sections(state)
        changed: dict[str, Any] = {}
        for name, body in sections.items():
            digest = _digest(body)
            if self._digests.get(name) != digest:
                self._digests[name] = digest
                changed[name] = body
        dropped = [name for name in self._digests if name not in sections]
        for name in dropped:
            self._digests.pop(name, None)
        if not changed and not dropped:
            return None
        self.seq += 1
        return {
            "type": "patch",
            "seq": self.seq,
            "timestamp": state.get("timestamp") or utc_now_iso(),
            "sections": changed,
            "removed": dropped,
        }


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
            "forecast_horizons": {}, "candidates": [],
            "decision": {"action": "WAITING", "reason": "engine_snapshot_missing", "gates": []},
            "promotion": {"status": "NOT_RUN", "gates": [], "failed_gates": []},
            "replay": {"status": "NOT_RUN"},
            "security": {"execution_mode": "UNKNOWN", "live_authorization": False},
            "broker_reconciliation": {},
            "constituent_attribution": [],
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

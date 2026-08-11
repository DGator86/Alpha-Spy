from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any

from .config import ForecastHorizonSpec, SuiteConfig
from .db import Journal
from .timeutil import ET
from .tradier import TradierClient


def _calendar_days(payload: dict[str, Any]) -> list[dict[str, Any]]:
    days = payload.get("days", {}).get("day") if isinstance(payload.get("days"), dict) else None
    if days is None:
        return []
    return days if isinstance(days, list) else [days]


class MarketCalendarResolver:
    """Use Tradier's production market calendar and cache it in the journal controls."""

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal

    def month(self, year: int, month: int) -> tuple[dict[str, Any], str]:
        key = f"market_calendar:{year:04d}-{month:02d}"
        cached = self.journal.get_control(key)
        if cached:
            try:
                return json.loads(cached), "journal_cache"
            except ValueError:
                pass
        if self.config.tradier.market_access_token.get_secret_value():
            try:
                with TradierClient(self.config, purpose="market") as client:
                    value = client.calendar(year, month)
                self.journal.set_control(key, json.dumps(value, sort_keys=True))
                return value, "tradier_production"
            except Exception:
                pass
        return {}, "weekday_fallback"

    def open_days(self, start: datetime, count: int) -> tuple[list[datetime], str]:
        local = start.astimezone(ET)
        output: list[datetime] = []
        source = "tradier_production"
        cursor = local.date()
        months: dict[tuple[int, int], tuple[dict[str, Any], str]] = {}
        for _ in range(370):
            key = (cursor.year, cursor.month)
            if key not in months:
                months[key] = self.month(*key)
            calendar, month_source = months[key]
            if month_source != "tradier_production" and source == "tradier_production":
                source = month_source
            open_status: bool | None = None
            for day in _calendar_days(calendar):
                if str(day.get("date")) == cursor.isoformat():
                    open_status = str(day.get("status") or "").lower() == "open"
                    break
            if open_status is None:
                open_status = cursor.weekday() < 5
                if source == "tradier_production":
                    source = "weekday_fallback"
            if open_status and cursor >= local.date():
                output.append(datetime.combine(cursor, time(9, 30), tzinfo=ET))
                if len(output) >= count:
                    break
            cursor += timedelta(days=1)
        return output, source

    def target(self, created_at: datetime, spec: ForecastHorizonSpec) -> tuple[datetime, dict[str, Any]]:
        local = created_at.astimezone(ET)
        close_today = datetime.combine(local.date(), time(16, 0), tzinfo=ET)
        if spec.name == "EOD":
            target = max(local + timedelta(minutes=1), close_today)
            return target, {"calendar_source": "same_session_close", "effective_minutes": max(1, int((target-local).total_seconds()/60))}
        if spec.name in {"1D", "5D"}:
            sessions = 1 if spec.name == "1D" else 5
            open_days, source = self.open_days(local + timedelta(days=1), sessions)
            if len(open_days) >= sessions:
                target_date = open_days[sessions - 1].date()
                target = datetime.combine(target_date, local.timetz().replace(tzinfo=None), tzinfo=ET)
                # If the source observation was late in the session, keep the same wall-clock time.
                if target.time() > time(16, 0):
                    target = datetime.combine(target_date, time(16, 0), tzinfo=ET)
                return target, {"calendar_source": source, "sessions_forward": sessions, "effective_minutes": spec.minutes}
        target = local + timedelta(minutes=spec.minutes)
        if target > close_today and spec.minutes <= 390:
            target = close_today
        return target, {"calendar_source": "intraday", "effective_minutes": max(1, int((target-local).total_seconds()/60))}

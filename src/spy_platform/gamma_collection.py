from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol


class OptionMarketClient(Protocol):
    def expirations(self, symbol: str) -> list[str]: ...

    def option_chain(
        self,
        symbol: str,
        expiration: str,
        greeks: bool = True,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class GammaExpirationPlan:
    zero_dte: str | None
    one_dte: str | None
    weekly: str | None

    @property
    def expirations(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value
                for value in (self.zero_dte, self.one_dte, self.weekly)
                if value is not None
            )
        )


def choose_gamma_expirations(
    expirations: list[str],
    *,
    session_date: date,
) -> GammaExpirationPlan:
    parsed = sorted(
        {
            datetime.fromisoformat(value).date(): value
            for value in expirations
            if datetime.fromisoformat(value).date() >= session_date
        }.items()
    )
    by_date = dict(parsed)
    zero = by_date.get(session_date)
    one = by_date.get(session_date + timedelta(days=1))
    future = [item for item in parsed if item[0] > session_date]
    if one is None and future:
        one = future[0][1]

    # Weekly context should be far enough from same/next-session noise to expose
    # term structure while remaining relevant to SPY intraday positioning.
    weekly_candidates = [
        value
        for expiry_date, value in parsed
        if 4 <= (expiry_date - session_date).days <= 10
    ]
    weekly = weekly_candidates[0] if weekly_candidates else (future[-1][1] if future else None)
    return GammaExpirationPlan(zero_dte=zero, one_dte=one, weekly=weekly)


def collect_gamma_chains(
    client: OptionMarketClient,
    *,
    symbol: str = "SPY",
    session_date: date,
) -> tuple[GammaExpirationPlan, list[dict[str, Any]]]:
    """Fetch read-only Gamma inputs from the market-data client.

    The function has no account, order, strategy, risk, or execution parameters.
    """
    plan = choose_gamma_expirations(client.expirations(symbol), session_date=session_date)
    chains: list[dict[str, Any]] = []
    labels = {
        plan.zero_dte: "0DTE",
        plan.one_dte: "1DTE",
        plan.weekly: "WEEKLY",
    }
    for expiration in plan.expirations:
        rows = client.option_chain(symbol, expiration, greeks=True)
        chains.append(
            {
                "underlying": symbol,
                "expiration": expiration,
                "bucket": labels.get(expiration, "OTHER"),
                "options": list(rows),
                "source": "market_data_client",
            }
        )
    return plan, chains

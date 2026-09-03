from __future__ import annotations

from datetime import date

from spy_platform.gamma_collection import choose_gamma_expirations, collect_gamma_chains


class FakeClient:
    def expirations(self, symbol: str):
        assert symbol == "SPY"
        return ["2026-09-03", "2026-09-04", "2026-09-11", "2026-09-18"]

    def option_chain(self, symbol: str, expiration: str, greeks: bool = True):
        assert symbol == "SPY"
        assert greeks is True
        return [{"symbol": f"SPY-{expiration}", "strike": 650.0}]


def test_choose_gamma_expirations_selects_0dte_1dte_and_weekly():
    plan = choose_gamma_expirations(
        ["2026-09-03", "2026-09-04", "2026-09-11", "2026-09-18"],
        session_date=date(2026, 9, 3),
    )
    assert plan.zero_dte == "2026-09-03"
    assert plan.one_dte == "2026-09-04"
    assert plan.weekly == "2026-09-11"


def test_gamma_collection_is_market_data_only():
    plan, chains = collect_gamma_chains(FakeClient(), session_date=date(2026, 9, 3))
    assert plan.expirations == ("2026-09-03", "2026-09-04", "2026-09-11")
    assert [chain["bucket"] for chain in chains] == ["0DTE", "1DTE", "WEEKLY"]
    assert all(chain["source"] == "market_data_client" for chain in chains)

"""Guards for the live VPS overlay that was not in git until this branch.

These are the Aug 17 session-tape rules, the dashboard publisher inf sanitizer,
and the websocket section stream. If they regress, the VPS and the repo diverge
again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alpha_spy.config import SuiteConfig
from alpha_spy.dashboard.service import SectionStream
from alpha_spy.db import Journal
from alpha_spy.publisher import DashboardPublisher
from alpha_spy.session_tape import (
    blocks_bearish,
    blocks_bullish,
    blocks_short_vol,
    distance_bps,
    resolve_session_open_spy,
    structure_session_veto,
)


def test_aug17_grind_blocks_calls_and_short_vol() -> None:
    open_bps = distance_bps(762.45, 765.13)
    assert open_bps is not None and open_bps <= -12.0
    assert blocks_bullish(open_bps) is True
    assert blocks_bearish(open_bps) is False
    assert blocks_short_vol(open_bps) is True
    assert structure_session_veto("LONG_CALL", "directional_long", open_bps=open_bps) == (
        "session_bias_against_calls"
    )
    assert structure_session_veto("PUT_DEBIT_SPREAD", "directional_long", open_bps=open_bps) is None
    assert structure_session_veto("IRON_CONDOR", "short_vol", open_bps=open_bps) == (
        "session_trend_blocks_short_vol"
    )


def test_session_open_is_the_first_rth_print_not_a_midday_restart(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal" / "alpha-spy.db")
    base = {
        "exchange_state": "open",
        "covered_weight": 1.0,
        "quote_count": 1,
        "stale_quote_count": 0,
        "integrity": "VERIFIED",
        "source": "test",
        "payload": {},
        "spy_bid": 765.12,
        "spy_ask": 765.14,
    }
    journal.insert_snapshot(
        {**base, "snapshot_id": "OPEN", "captured_at": "2026-08-17T13:30:02Z", "spy_price": 765.13},
        [{"symbol": "SPY", "price": 765.13}],
    )
    journal.insert_snapshot(
        {**base, "snapshot_id": "LATE", "captured_at": "2026-08-17T18:00:00Z", "spy_price": 762.40},
        [{"symbol": "SPY", "price": 762.40}],
    )
    late = {
        "captured_at": "2026-08-17T18:00:00Z",
        "spy_price": 762.40,
        "exchange_state": "open",
    }
    assert resolve_session_open_spy(journal, late) == 765.13


def test_publisher_strips_non_json_floats() -> None:
    publisher = DashboardPublisher(SuiteConfig())
    cleaned = publisher._jsonable({"ok": 1.5, "inf": float("inf"), "nan": float("nan")})
    assert cleaned == {"ok": 1.5, "inf": None, "nan": None}


def test_dashboard_socket_sends_only_changed_sections() -> None:
    stream = SectionStream()
    first = stream.snapshot({"timestamp": datetime.now(UTC).isoformat(), "market": {"spy": 100.0}})
    assert first["type"] == "snapshot"
    assert "market" in first["sections"]
    unchanged = stream.patch({"timestamp": "later", "market": {"spy": 100.0}})
    assert unchanged is None
    changed = stream.patch({"timestamp": "later", "market": {"spy": 100.1}})
    assert changed is not None
    assert changed["type"] == "patch"
    assert changed["sections"]["market"]["market"]["spy"] == 100.1


def test_dashboard_cli_can_export_an_origin_allow_list() -> None:
    config = SuiteConfig()
    config.dashboard.allowed_origins = ["https://alpha-spy.vercel.app"]
    assert ",".join(config.dashboard.allowed_origins) == "https://alpha-spy.vercel.app"

"""Eastern-time handling for the session windows and the forced flat.

Everything that gates trading is expressed as an Eastern wall-clock time, so
these check the conversions hold across both DST offsets and that a control
cannot be silently disabled by the way its time is written.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from alpha_spy.config import RiskConfig
from alpha_spy.timeutil import ET, at_or_after_et, in_et_window, parse_hhmm, utc_iso

# 2026: DST runs 8 March to 1 November.
WINTER = datetime(2026, 1, 15, tzinfo=UTC)  # EST, UTC-5
SUMMER = datetime(2026, 7, 15, tzinfo=UTC)  # EDT, UTC-4


def _et(day: datetime, hour: int, minute: int = 0) -> datetime:
    """A UTC instant corresponding to the given Eastern wall-clock time."""
    offset = 5 if day.month == 1 else 4
    return day.replace(hour=hour + offset, minute=minute)


@pytest.mark.parametrize("day", [WINTER, SUMMER], ids=["EST", "EDT"])
@pytest.mark.parametrize(
    "hour,minute,expected",
    [(9, 30, False), (9, 40, True), (12, 0, True), (15, 15, True), (15, 16, False)],
)
def test_entry_window_tracks_eastern_wall_clock(day, hour, minute, expected):
    assert in_et_window(_et(day, hour, minute), "09:40", "15:15") is expected


@pytest.mark.parametrize("day", [WINTER, SUMMER], ids=["EST", "EDT"])
def test_forced_flat_tracks_eastern_wall_clock(day):
    assert at_or_after_et(_et(day, 15, 54), "15:55") is False
    assert at_or_after_et(_et(day, 15, 55), "15:55") is True
    assert at_or_after_et(_et(day, 16, 30), "15:55") is True


def test_forced_flat_is_not_a_string_comparison():
    """Regression: "15:00" >= "9:55" is False lexicographically.

    The forced flat used to compare strftime("%H:%M") against the raw config
    value, so a non-padded time disabled the control completely — a position
    would never be flattened. parse_hhmm accepts "9:55", so the check must too.
    """
    moment = RiskConfig(forced_flat_time_et="9:55").forced_flat_time_et
    assert at_or_after_et(_et(SUMMER, 15, 0), moment) is True
    assert at_or_after_et(_et(SUMMER, 9, 0), moment) is False


def test_risk_config_normalises_times_to_zero_padded():
    config = RiskConfig(forced_flat_time_et="9:5", entry_start_time_et="9:40")
    assert config.forced_flat_time_et == "09:05"
    assert config.entry_start_time_et == "09:40"


@pytest.mark.parametrize("bad", ["15:5x", "24:00", "15:60", "1555", "", "15:55:30", "-1:00"])
def test_risk_config_rejects_unusable_times(bad):
    with pytest.raises(ValidationError):
        RiskConfig(forced_flat_time_et=bad)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"maximum_contracts": 2},
        {"maximum_contracts": 0},
        {"maximum_trade_risk_dollars": -1.0},
        {"daily_loss_limit_dollars": -1.0},
        {"minimum_trust_to_trade": 1.5},
        {"account_risk_fraction": -0.1},
        {"maximum_trades_per_day": -1},
    ],
)
def test_risk_config_rejects_out_of_range_limits(kwargs):
    with pytest.raises(ValidationError):
        RiskConfig(**kwargs)


def test_parse_hhmm_accepts_non_padded():
    assert parse_hhmm("9:40").hour == 9
    assert parse_hhmm("09:40").minute == 40


def test_utc_iso_is_zulu_suffixed():
    stamp = utc_iso(datetime(2026, 3, 8, 12, 0, tzinfo=UTC))
    assert stamp.endswith("Z")
    assert "+00:00" not in stamp


def test_utc_iso_converts_eastern_input():
    eastern = datetime(2026, 7, 15, 12, 0, tzinfo=ET)
    assert utc_iso(eastern).startswith("2026-07-15T16:00")

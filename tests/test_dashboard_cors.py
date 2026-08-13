"""Tests for the cross-origin allow list.

Hosting the workstation off-host (Vercel) means the browser calls the dashboard
from a different origin, which needs CORS. The dashboard serves account state
and accepts operator commands, so the parsing rules here are a safety control,
not a convenience: the default must be closed, and a wildcard must never be
honoured.
"""
from __future__ import annotations

from alpha_spy.dashboard.config import Settings


def _settings(value: str) -> Settings:
    return Settings(dashboard_allowed_origins=value)


def test_default_allows_no_cross_origin_access():
    # The loopback deployment is same-origin and needs none. An unset value must
    # mean "nothing", never "everything".
    assert Settings().allowed_origins == []


def test_wildcard_is_rejected_rather_than_honoured():
    assert _settings("*").allowed_origins == []
    assert _settings("https://alpha-spy.vercel.app,*").allowed_origins == [
        "https://alpha-spy.vercel.app"
    ]


def test_origins_are_split_trimmed_and_normalised():
    parsed = _settings(
        " https://alpha-spy.vercel.app/ , https://preview-abc.vercel.app ,, "
    ).allowed_origins
    # Trailing slashes are stripped because a browser's Origin header never has
    # one; leaving it on would silently fail to match every request.
    assert parsed == ["https://alpha-spy.vercel.app", "https://preview-abc.vercel.app"]


def test_empty_and_whitespace_only_values_stay_closed():
    for value in ("", "   ", ",", " , , "):
        assert _settings(value).allowed_origins == [], f"{value!r} should allow nothing"

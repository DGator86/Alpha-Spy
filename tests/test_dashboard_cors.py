"""Tests for the cross-origin allow list.

Hosting the workstation off-host (Vercel) means the browser calls the dashboard
from a different origin, which needs CORS. The dashboard serves account state
and accepts operator commands, so the parsing rules here are a safety control,
not a convenience: the default must be closed, and a wildcard must never be
honoured.
"""
from __future__ import annotations

import os

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


def test_allowed_origins_from_config_yaml_reach_the_dashboard(tmp_path, monkeypatch):
    """The VPS configures the dashboard through config.yaml, not the environment.

    `alpha-spy dashboard-api` exports the dashboard section into the process
    environment before starting uvicorn. `allowed_origins` has to be part of
    that export, or an operator who adds it to config.yaml - the only place
    every other dashboard setting lives - gets silence.
    """
    from alpha_spy.cli import _configure_dashboard_env
    from alpha_spy.config import SuiteConfig

    config = SuiteConfig()
    config.dashboard.allowed_origins = ["https://alpha-spy.vercel.app"]
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
    _configure_dashboard_env(config)

    assert Settings().allowed_origins == ["https://alpha-spy.vercel.app"]
    assert os.environ["TRADIER_ENV"] == config.tradier.market_environment


def test_empty_config_list_does_not_clobber_the_env_file(tmp_path, monkeypatch):
    """os.environ wins over the Settings env_file.

    Exporting an empty value unconditionally would silently override an allow
    list set in /etc/alpha-spy/dashboard.env, so the export is conditional.
    """
    from alpha_spy.cli import _configure_dashboard_env
    from alpha_spy.config import SuiteConfig

    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://set-in-env-file.example")
    config = SuiteConfig()
    assert config.dashboard.allowed_origins == []
    _configure_dashboard_env(config)

    assert Settings().allowed_origins == ["https://set-in-env-file.example"]

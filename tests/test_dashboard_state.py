"""Tests for the state surface the workstation reads.

`build_dashboard_state` gained a security block, a candidate book, a decision
record carrying its gate ladder, and the full validation gate list. The security
block is the one the UI paints as an unmissable banner, so it has to be correct
and it has to fail closed.
"""
from __future__ import annotations

from pathlib import Path

from alpha_spy.config import SuiteConfig
from alpha_spy.db import Journal
from alpha_spy.state import build_dashboard_state


def make_config(tmp_path: Path) -> SuiteConfig:
    config = SuiteConfig()
    config.paths.state_root = tmp_path
    config.paths.database = tmp_path / "journal" / "alpha-spy.db"
    config.paths.dashboard_database = tmp_path / "dashboard" / "cc.sqlite"
    config.paths.universe_cache = tmp_path / "reference" / "universe.csv"
    config.paths.model_dir = tmp_path / "models"
    config.paths.report_dir = tmp_path / "reports"
    config.paths.log_dir = tmp_path / "logs"
    config.paths.production_sentinel = tmp_path / "PRODUCTION_UNLOCKED"
    config.paths.production_approval = tmp_path / "PRODUCTION_APPROVED.json"
    config.create_directories()
    return config


def _state(tmp_path: Path) -> dict:
    config = make_config(tmp_path)
    return build_dashboard_state(config, Journal(config.paths.database))


def test_state_publishes_the_workstation_sections(tmp_path):
    state = _state(tmp_path)
    for key in ("security", "candidates", "decision", "promotion", "replay", "forecast_horizons"):
        assert key in state, f"{key} is missing; the workstation renders an empty panel without it"


def test_security_block_fails_closed_on_a_bare_install(tmp_path):
    security = _state(tmp_path)["security"]
    # Nothing has been unlocked, so nothing may claim authorization.
    assert security["live_authorization"] is False
    assert security["production_unlocked"] is False
    assert security["production_sentinel_present"] is False
    assert security["production_approval_valid"] is False
    # This one is a hard invariant of the whole design, not a default.
    assert security["automatic_live_enable"] is False


def test_sentinel_alone_does_not_authorize_live_trading(tmp_path):
    config = make_config(tmp_path)
    config.paths.production_sentinel.write_text("unlocked", encoding="utf-8")
    config.trading.submit_orders = True
    config.tradier.environment = "production"

    security = build_dashboard_state(config, Journal(config.paths.database))["security"]
    assert security["production_unlocked"] is True
    # The sentinel is necessary but not sufficient: the evidence-bound approval
    # artifact is still absent, so the banner must still read locked.
    assert security["production_approval_valid"] is False
    assert security["live_authorization"] is False


def test_decision_defaults_are_renderable_before_the_first_cycle(tmp_path):
    decision = _state(tmp_path)["decision"]
    # A cold start must not produce nulls the UI would print as "NaN"; it
    # produces an explicit waiting state with an empty ladder.
    assert decision["action"] == "WAITING"
    assert decision["reason"] == "no_decision_yet"
    assert decision["gates"] == []
    assert decision["failed_gates"] == []


def test_promotion_defaults_to_not_run_with_an_empty_gate_list(tmp_path):
    promotion = _state(tmp_path)["promotion"]
    assert promotion["status"] == "NOT_RUN"
    assert promotion["gates"] == []
    assert promotion["automatic_live_enable"] is False

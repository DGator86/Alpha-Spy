"""Tests for the documented fail-closed risk controls.

README.md and BUILD_REPORT.md make specific safety promises: one contract, sequential
same-day trades with one position at a time, a $100 maximum modelled trade risk, a
$200 managed daily-loss limit, time-gated RTH entries, and a production submission
path that cannot be opened from configuration alone. These exercise those
promises directly rather than trusting the prose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from alpha_spy.config import SuiteConfig
from alpha_spy.db import Journal
from alpha_spy.risk import (
    AccountState,
    allowed_risk,
    choose_decision,
    parse_account_state,
)


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
    config.create_directories()
    return config


# --------------------------------------------------------------------------
# parse_account_state
# --------------------------------------------------------------------------


def test_flat_account_parses_instead_of_raising():
    """Regression: open_pl == 0.0 is the normal flat state, not a missing key.

    The previous `or` chain fell through to `get("day_trade_buying_power_used")
    * 0.0`, which raised TypeError whenever that key was absent. The engine
    caught it, alerted "Account poll failed" and substituted a fabricated
    $25,000 equity, so live risk was sized off a phantom balance.
    """
    state = parse_account_state(
        {"balances": {"total_equity": 50_000.0, "total_cash": 20_000.0, "open_pl": 0.0}}
    )
    assert state.equity == pytest.approx(50_000.0)
    assert state.daily_pnl == pytest.approx(0.0)


def test_account_with_no_pnl_keys_parses():
    state = parse_account_state({"balances": {"total_equity": 50_000.0}})
    assert state.daily_pnl == pytest.approx(0.0)


def test_zero_equity_is_not_replaced_by_the_default():
    """Regression: a real zero balance must not become the $25,000 fallback."""
    state = parse_account_state(
        {"balances": {"total_equity": 0.0, "total_cash": 0.0, "open_pl": -5_000.0}}
    )
    assert state.equity == pytest.approx(0.0)
    assert state.cash == pytest.approx(0.0)


def test_absent_balances_still_fall_back_to_the_default():
    assert parse_account_state({}).equity == pytest.approx(25_000.0)
    assert parse_account_state(None).equity == pytest.approx(25_000.0)
    assert parse_account_state({"balances": None}).equity == pytest.approx(25_000.0)


def test_nested_balances_envelope_and_string_numbers():
    state = parse_account_state({"balances": {"balances": {"total_equity": "31000.0", "open_pl": "12.5"}}})
    assert state.equity == pytest.approx(31_000.0)
    assert state.daily_pnl == pytest.approx(12.5)


# --------------------------------------------------------------------------
# allowed_risk
# --------------------------------------------------------------------------


def test_allowed_risk_is_capped_by_the_configured_maximum(tmp_path):
    config = make_config(tmp_path)
    # 10,000,000 * 0.0025 = 25,000, far above the $100 cap.
    account = AccountState(10_000_000.0, 10_000_000.0, 10_000_000.0, 0.0)
    assert allowed_risk(config, account, 1.0, "GREEN") == pytest.approx(
        config.risk.maximum_trade_risk_dollars
    )


def test_zero_equity_yields_zero_allowed_risk(tmp_path):
    config = make_config(tmp_path)
    account = AccountState(0.0, 0.0, 0.0, 0.0)
    assert allowed_risk(config, account, 1.0, "GREEN") == pytest.approx(0.0)


@pytest.mark.parametrize(
    "health,expected_factor",
    [("GREEN", 1.0), ("YELLOW", 0.50), ("ORANGE", 0.25), ("RED", 0.0), ("UNKNOWN", 0.0)],
)
def test_health_state_scales_allowed_risk(tmp_path, health, expected_factor):
    config = make_config(tmp_path)
    account = AccountState(20_000.0, 20_000.0, 20_000.0, 0.0)
    base = min(config.risk.maximum_trade_risk_dollars, 20_000.0 * config.risk.account_risk_fraction)
    assert allowed_risk(config, account, 1.0, health) == pytest.approx(base * expected_factor)


def test_negative_configured_risk_fails_closed(tmp_path):
    config = make_config(tmp_path)
    config.risk.maximum_trade_risk_dollars = -100.0
    account = AccountState(20_000.0, 20_000.0, 20_000.0, 0.0)
    assert allowed_risk(config, account, 1.0, "GREEN") == pytest.approx(0.0)


# --------------------------------------------------------------------------
# choose_decision gating
# --------------------------------------------------------------------------


def _prediction() -> dict:
    return {"prediction_id": "P-test"}


def _feature(health: str = "GREEN", trust: float = 1.0) -> dict:
    return {"health_state": health, "trust_score": trust}


def _candidate(max_loss: float = 50.0, score: float = 1.0) -> dict:
    return {
        "candidate_id": "C-test",
        "status": "ELIGIBLE",
        "max_loss": max_loss,
        "score": score,
    }


def _decide(config, journal, *, feature=None, candidates=None, account=None):
    return choose_decision(
        config,
        journal,
        _prediction(),
        feature or _feature(),
        candidates if candidates is not None else [_candidate()],
        account or AccountState(50_000.0, 50_000.0, 50_000.0, 0.0),
    )


def test_operator_pause_blocks_entry(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    journal.set_control("entries_paused", "true")
    decision = _decide(config, journal)
    assert decision["action"] == "NO_TRADE"
    assert decision["reason"] == "operator_paused"


def test_daily_loss_limit_blocks_entry(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    account = AccountState(
        50_000.0, 50_000.0, 50_000.0, -abs(config.risk.daily_loss_limit_dollars)
    )
    decision = _decide(config, journal, account=account)
    assert decision["action"] == "NO_TRADE"
    assert decision["reason"] in {"daily_loss_limit", "outside_entry_window"}


def test_low_trust_blocks_entry(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    feature = _feature(trust=config.risk.minimum_trust_to_trade - 0.01)
    decision = _decide(config, journal, feature=feature)
    assert decision["action"] == "NO_TRADE"
    assert decision["reason"] in {"trust_below_threshold", "outside_entry_window"}


@pytest.mark.parametrize("health", ["YELLOW", "ORANGE", "RED"])
def test_non_green_health_blocks_entry(tmp_path, health):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    decision = _decide(config, journal, feature=_feature(health=health))
    assert decision["action"] == "NO_TRADE"


def test_candidate_above_allowed_risk_is_rejected(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    # Well above the $100 configured maximum trade risk.
    decision = _decide(config, journal, candidates=[_candidate(max_loss=10_000.0)])
    assert decision["action"] == "NO_TRADE"
    assert decision["reason"] in {"no_candidate_within_allowed_risk", "outside_entry_window"}


def test_non_eligible_candidates_are_ignored(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    shadow = _candidate()
    shadow["status"] = "SHADOW"
    decision = _decide(config, journal, candidates=[shadow])
    assert decision["action"] == "NO_TRADE"


def test_decision_never_exceeds_allowed_risk(tmp_path):
    """Whatever is chosen, its modelled loss is within the allowed risk."""
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    candidates = [_candidate(max_loss=loss, score=loss) for loss in (10.0, 60.0, 500.0)]
    decision = _decide(config, journal, candidates=candidates)
    if decision["candidate_id"] is not None:
        chosen = next(c for c in candidates if c["candidate_id"] == decision["candidate_id"])
        assert float(chosen["max_loss"]) <= decision["allowed_risk"] + 1e-9


# --------------------------------------------------------------------------
# Production lock
# --------------------------------------------------------------------------


def _production_ready(config: SuiteConfig) -> None:
    """Everything the production gate demands, so tests can remove one at a time."""
    from pydantic import SecretStr

    config.tradier.environment = "production"
    config.tradier.access_token = SecretStr("token")
    config.tradier.account_id = "ACC1"
    config.trading.enabled = True
    config.trading.submit_orders = True
    config.trading.paper_mode = False
    config.risk.maximum_contracts = 1
    config.paths.production_sentinel.write_text("unlocked", encoding="utf-8")


def test_fully_configured_production_passes_the_gate(tmp_path):
    config = make_config(tmp_path)
    _production_ready(config)
    config.assert_live_trading_safe()  # must not raise


def test_gate_is_inert_while_submission_is_disabled(tmp_path):
    """The shipped posture never reaches the gate, because it never submits."""
    config = make_config(tmp_path)
    assert config.trading.enabled is False
    assert config.trading.submit_orders is False
    config.assert_live_trading_safe()  # returns early, does not raise


@pytest.mark.parametrize(
    "break_it,fragment",
    [
        (lambda c: setattr(c.tradier, "environment", "sandbox"), "not production"),
        (lambda c: setattr(c.trading, "paper_mode", True), "paper_mode"),
        (lambda c: setattr(c.tradier, "account_id", ""), "account id"),
        (lambda c: c.paths.production_sentinel.unlink(), "sentinel"),
        (lambda c: setattr(c.risk, "maximum_contracts", 2), "maximum_contracts"),
    ],
)
def test_each_missing_precondition_locks_production(tmp_path, break_it, fragment):
    config = make_config(tmp_path)
    _production_ready(config)
    break_it(config)
    with pytest.raises(RuntimeError, match="Live trading locked") as excinfo:
        config.assert_live_trading_safe()
    assert fragment in str(excinfo.value)


def test_missing_access_token_locks_production(tmp_path):
    from pydantic import SecretStr

    config = make_config(tmp_path)
    _production_ready(config)
    config.tradier.access_token = SecretStr("")
    with pytest.raises(RuntimeError, match="access token is missing"):
        config.assert_live_trading_safe()


def test_production_sentinel_must_be_a_file(tmp_path):
    config = make_config(tmp_path)
    _production_ready(config)
    config.paths.production_sentinel.unlink()
    config.paths.production_sentinel.mkdir()
    assert config.production_is_unlocked() is False

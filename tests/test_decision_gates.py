"""Tests for the published entry-gate ladder.

`choose_decision` used to short-circuit on the first failing check, so a
NO_TRADE told an operator one thing that was wrong and nothing about the other
ten. The decision record now carries every gate. These pin the two properties
that makes worth anything: the ladder is complete regardless of where it fails,
and the headline `reason` still matches the first failure exactly, because the
journal and the existing risk tests are written against those strings.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alpha_spy.config import SuiteConfig
from alpha_spy.db import Journal
from alpha_spy.risk import AccountState, choose_decision, evaluate_entry_gates

# Inside the default 09:45-15:30 ET entry window (13:40Z is 09:40 EDT... use
# 15:00Z = 11:00 ET) so the window gate is not the thing under test.
IN_WINDOW = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)


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


def _feature(health: str = "GREEN", trust: float = 1.0) -> dict:
    return {"health_state": health, "trust_score": trust}


def _candidate(max_loss: float = 50.0, score: float = 1.0) -> dict:
    return {"candidate_id": "C-test", "status": "ELIGIBLE", "max_loss": max_loss, "score": score}


def _decide(config, journal, *, feature=None, candidates=None, account=None, now=IN_WINDOW):
    return choose_decision(
        config,
        journal,
        {"prediction_id": "P-test"},
        feature or _feature(),
        candidates if candidates is not None else [_candidate()],
        account or AccountState(50_000.0, 50_000.0, 50_000.0, 0.0),
        now=now,
    )


def test_every_gate_is_published_on_a_clean_pass(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    decision = _decide(config, journal)

    gates = decision["payload"]["gates"]
    assert decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"}
    assert decision["payload"]["failed_gates"] == []
    assert all(gate["passed"] for gate in gates)
    # A passing gate still has to say what it measured; the workstation renders
    # the detail line whether the gate passed or not.
    assert all(gate["detail"] for gate in gates)
    assert {gate["kind"] for gate in gates} == {"veto", "qualifier"}


def test_ladder_is_complete_even_when_the_first_gate_fails(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    journal.set_control("entries_paused", "true")
    decision = _decide(config, journal)

    gates = decision["payload"]["gates"]
    assert decision["reason"] == "operator_paused"
    # The whole point: an operator pause must not hide the state of the other
    # checks. Every gate is still evaluated and reported.
    assert len(gates) >= 11
    assert [gate["name"] for gate in gates if not gate["passed"]] == ["operator_paused"]
    assert decision["payload"]["failed_gates"] == ["operator_paused"]


def test_reason_is_the_first_failure_when_several_gates_fail(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    journal.set_control("entries_paused", "true")
    # Also break trust and the candidate book, both of which sit later in the
    # ladder than the operator pause.
    decision = _decide(
        config,
        journal,
        feature=_feature(trust=0.0),
        candidates=[],
    )

    failed = decision["payload"]["failed_gates"]
    assert len(failed) > 1
    assert decision["reason"] == failed[0] == "operator_paused"


def test_gate_names_and_reasons_cover_the_documented_reason_codes(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    gates, _, _ = evaluate_entry_gates(
        config, journal, _feature(), [_candidate()], AccountState(50_000.0, 50_000.0, 50_000.0, 0.0), now=IN_WINDOW
    )
    reasons = {gate["reason"] for gate in (g.as_dict() for g in gates)}
    assert {
        "operator_paused",
        "outside_entry_window",
        "account_state_invalid",
        "no_buying_power",
        "daily_loss_limit",
        "daily_trade_limit",
        "trust_below_threshold",
        "zero_allowed_risk",
        "managed_position_already_open",
        "no_eligible_candidate",
        "no_candidate_within_allowed_risk",
    } <= reasons


def test_health_reason_stays_dynamic(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    decision = _decide(config, journal, feature=_feature(health="ORANGE"))
    # allowed_risk collapses to zero for a non-GREEN multiplier in some configs,
    # so accept either the health gate or the risk-budget gate that follows it —
    # both are legitimate first failures and both must be named exactly.
    assert decision["reason"] in {"health_orange", "zero_allowed_risk"}
    names = [gate["name"] for gate in decision["payload"]["gates"] if not gate["passed"]]
    assert "health_state" in names


def test_affordable_count_reflects_the_risk_budget(tmp_path):
    config = make_config(tmp_path)
    journal = Journal(config.paths.database)
    # One structure inside the budget, one far outside it.
    decision = _decide(
        config,
        journal,
        candidates=[_candidate(max_loss=10.0, score=0.5), _candidate(max_loss=10_000.0, score=9.9)],
    )
    assert decision["payload"]["considered_candidates"] == 2
    assert decision["payload"]["affordable_candidates"] == 1
    # The unaffordable structure scores higher, so this also proves selection
    # happens after the risk filter and not before it.
    assert decision["payload"]["candidate"]["max_loss"] == 10.0

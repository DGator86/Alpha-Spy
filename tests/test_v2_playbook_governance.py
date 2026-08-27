from datetime import UTC, datetime, timedelta

from alpha_spy.db import Journal
from alpha_spy.v2_playbook_governance import evaluate_playbooks


def _insert_closed(journal, *, index: int, playbook: str, pnl: float, process_score: float = 0.9):
    opened = datetime(2026, 8, 1, 14, 0, tzinfo=UTC) + timedelta(days=index)
    payload = {
        "candidate": {"payload": {"trade_thesis": {"playbook": playbook}}},
        "post_trade_review": {
            "process_score": process_score,
            "component_attribution": {
                "duration_forecast": {"status": "PASS"},
                "transition_forecast": {"status": "PASS"},
                "regime_identification": {"status": "UNKNOWN"},
            },
        },
    }
    with journal.transaction() as con:
        con.execute(
            """
            INSERT INTO positions(
                position_id,decision_id,broker_order_id,opened_at,closed_at,status,
                strategy,quantity,entry_value,current_value,realized_pnl,unrealized_pnl,
                max_profit,max_loss,mfe,mae,exit_reason,legs_json,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"POS-{index}",
                None,
                None,
                opened.isoformat(),
                (opened + timedelta(minutes=15)).isoformat(),
                "CLOSED",
                "CALL_DEBIT_SPREAD",
                1,
                0.50,
                0.60,
                pnl,
                0.0,
                100.0,
                50.0,
                max(0.0, pnl + 2.0),
                min(0.0, pnl - 2.0),
                "test_exit",
                "[]",
                journal._json(payload),
            ),
        )


def test_tiny_sample_cannot_promote_playbook(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(7):
        _insert_closed(journal, index=index, playbook="TEST_PLAYBOOK", pnl=10.0)
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["status"] == "EXPERIMENTAL"
    assert status["execution_eligible"] is False


def test_twenty_positive_process_quality_examples_become_provisional(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(20):
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=8.0 if index % 4 else -3.0,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["samples"] == 20
    assert status["mean_pnl"] > 0
    assert status["status"] == "PROVISIONAL_REPEATABLE"
    assert status["execution_eligible"] is True


def test_negative_realized_action_value_is_narrowed_not_promoted(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(20):
        _insert_closed(journal, index=index, playbook="TEST_PLAYBOOK", pnl=-2.0)
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["status"] == "NARROW_OR_RETIRE"
    assert status["execution_eligible"] is False

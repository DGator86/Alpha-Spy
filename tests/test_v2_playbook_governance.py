from datetime import UTC, datetime, timedelta

from alpha_spy.db import Journal
from alpha_spy.v2_playbook_governance import evaluate_playbooks
from alpha_spy.v2_policy import CURRENT_POLICY_VERSION


def _insert_closed(
    journal,
    *,
    index: int,
    playbook: str,
    pnl: float,
    process_score: float = 0.9,
    forward_actual_chain: bool = False,
    session_index: int | None = None,
    policy_version: str = CURRENT_POLICY_VERSION,
):
    day_index = index if session_index is None else session_index
    opened = datetime(2026, 8, 1, 14, 0, tzinfo=UTC) + timedelta(days=day_index)
    provenance = (
        {
            "evidence_class": "FORWARD_ACTUAL_CHAIN",
            "actual_chain": True,
            "chain_snapshot_id": f"OC-{index}",
            "policy_version": policy_version,
        }
        if forward_actual_chain
        else {
            "evidence_class": "REPLAY_OR_UNVERIFIED",
            "actual_chain": False,
            "policy_version": policy_version,
        }
    )
    payload = {
        "candidate": {
            "payload": {
                "trade_thesis": {
                    "playbook": playbook,
                    "policy_version": policy_version,
                    "evidence_provenance": provenance,
                }
            }
        },
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
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=10.0,
            forward_actual_chain=True,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["status"] == "EXPERIMENTAL"
    assert status["execution_eligible"] is False


def test_twenty_positive_replay_examples_remain_challenger(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(20):
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=8.0 if index % 4 else -3.0,
            forward_actual_chain=False,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["samples"] == 20
    assert status["mean_pnl"] > 0
    assert status["forward_actual_chain_samples"] == 0
    assert status["status"] == "CHALLENGER"
    assert status["execution_eligible"] is False


def test_old_policy_forward_examples_cannot_promote_current_policy(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(40):
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=10.0,
            forward_actual_chain=True,
            policy_version="alpha-v2-old-policy",
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["forward_actual_chain_samples"] == 0
    assert status["policy_version"] == CURRENT_POLICY_VERSION
    assert status["status"] == "CHALLENGER"
    assert status["execution_eligible"] is False


def test_twenty_positive_forward_actual_chain_examples_become_provisional(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(20):
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=8.0 if index % 4 else -3.0,
            forward_actual_chain=True,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["forward_actual_chain_samples"] == 20
    assert status["forward_sessions"] == 20
    assert status["forward_mean_pnl"] > 0
    assert status["forward_session_pnl_lcb90"] > 0
    assert status["status"] == "PROVISIONAL_REPEATABLE"
    assert status["execution_eligible"] is True


def test_forty_robust_forward_examples_can_validate(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(40):
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=7.0 if index % 4 else -3.0,
            forward_actual_chain=True,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["forward_actual_chain_samples"] == 40
    assert status["forward_sessions"] == 40
    assert status["forward_profit_factor"] is not None
    assert status["forward_profit_factor"] >= 1.20
    assert status["forward_session_pnl_lcb95"] > 0
    assert status["status"] == "VALIDATED_PLAYBOOK"
    assert status["execution_eligible"] is True


def test_many_correlated_trades_from_few_sessions_cannot_validate(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(40):
        _insert_closed(
            journal,
            index=index,
            session_index=index // 8,
            playbook="TEST_PLAYBOOK",
            pnl=8.0,
            forward_actual_chain=True,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["forward_actual_chain_samples"] == 40
    assert status["forward_sessions"] == 5
    assert status["status"] == "CHALLENGER"
    assert status["execution_eligible"] is False
    assert "fewer_than_10_current_policy_independent_forward_sessions" in status["reasons"]


def test_positive_mean_with_wide_uncertainty_does_not_validate(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(40):
        pnl = 100.0 if index % 5 in {0, 1, 2} else -110.0
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=pnl,
            forward_actual_chain=True,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["forward_mean_pnl"] > 0
    assert status["forward_win_rate"] >= 0.50
    assert status["forward_profit_factor"] >= 1.20
    assert status["forward_session_pnl_lcb95"] <= 0
    assert status["status"] == "CHALLENGER"
    assert status["execution_eligible"] is False


def test_negative_forward_action_value_is_narrowed_not_promoted(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    for index in range(20):
        _insert_closed(
            journal,
            index=index,
            playbook="TEST_PLAYBOOK",
            pnl=-2.0,
            forward_actual_chain=True,
        )
    status = evaluate_playbooks(journal)["TEST_PLAYBOOK"]
    assert status["status"] == "NARROW_OR_RETIRE"
    assert status["execution_eligible"] is False

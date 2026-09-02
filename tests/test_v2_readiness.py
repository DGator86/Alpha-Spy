from alpha_spy import v2_readiness
from alpha_spy.v2_policy import CURRENT_POLICY_VERSION


class _NoLifecycleJournal:
    def session(self):
        raise RuntimeError("no lifecycle database")


def _playbook(status: str, *, eligible: bool, lcb90: float = 0.0, lcb95: float = 0.0):
    return {
        "status": status,
        "execution_eligible": eligible,
        "policy_version": CURRENT_POLICY_VERSION,
        "forward_session_pnl_lcb90": lcb90,
        "forward_session_pnl_lcb95": lcb95,
    }


def test_readiness_never_calls_historical_backtest_profitable(monkeypatch):
    monkeypatch.setattr(
        v2_readiness,
        "evaluate_playbooks",
        lambda journal: {
            "DIRECTIONAL_MOMENTUM": _playbook("CHALLENGER", eligible=False),
        },
    )
    result = v2_readiness.evaluate_readiness(_NoLifecycleJournal())
    assert result["profitability"] == "UNPROVEN"
    assert result["live_capital_eligible"] is False


def test_provisional_requires_current_policy_90pct_forward_bound(monkeypatch):
    monkeypatch.setattr(
        v2_readiness,
        "evaluate_playbooks",
        lambda journal: {
            "DIRECTIONAL_MOMENTUM": _playbook(
                "PROVISIONAL_REPEATABLE",
                eligible=True,
                lcb90=2.0,
            ),
        },
    )
    result = v2_readiness.evaluate_readiness(_NoLifecycleJournal())
    assert result["profitability"] == "PROVISIONAL_FORWARD_EDGE"
    assert result["validated_profitable_playbooks"] == []


def test_profitable_label_requires_validated_95pct_forward_bound(monkeypatch):
    monkeypatch.setattr(
        v2_readiness,
        "evaluate_playbooks",
        lambda journal: {
            "DIRECTIONAL_MOMENTUM": _playbook(
                "VALIDATED_PLAYBOOK",
                eligible=True,
                lcb90=4.0,
                lcb95=1.0,
            ),
        },
    )
    result = v2_readiness.evaluate_readiness(_NoLifecycleJournal())
    assert result["profitability"] == "FORWARD_VALIDATED_PROFITABLE"
    assert result["validated_profitable_playbooks"] == ["DIRECTIONAL_MOMENTUM"]
    assert result["live_capital_eligible"] is False


def test_old_policy_cannot_receive_profitable_label(monkeypatch):
    old = _playbook("VALIDATED_PLAYBOOK", eligible=True, lcb90=4.0, lcb95=2.0)
    old["policy_version"] = "alpha-v2-old-policy"
    monkeypatch.setattr(
        v2_readiness,
        "evaluate_playbooks",
        lambda journal: {"DIRECTIONAL_MOMENTUM": old},
    )
    result = v2_readiness.evaluate_readiness(_NoLifecycleJournal())
    assert result["profitability"] == "UNPROVEN"

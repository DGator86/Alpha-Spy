import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "examples" / "apply_professional_policy.py"
    spec = importlib.util.spec_from_file_location("apply_professional_policy", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_professional_policy_excludes_post_entry_and_hidden_fields():
    module = _module()
    forbidden = module.FORBIDDEN_FEATURES
    features = set(module.NUMERIC_FEATURES)
    assert not forbidden.intersection(features)
    assert {"pnl", "mfe", "mae", "exit_reason", "regime"}.issubset(forbidden)


def test_policy_keeps_risk_finite_and_one_trade_per_world():
    module = _module()
    policy = module.LOCKED_POLICY
    assert policy["maximum_loss_per_trade"] <= 900.0
    assert policy["maximum_trades_per_world"] == 1
    assert policy["credit"]["minimum_predicted_profit_factor"] > 1.0
    assert policy["debit"]["minimum_predicted_profit_factor"] > 1.0

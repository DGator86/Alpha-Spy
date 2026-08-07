import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_high_win_policy_excludes_hidden_simulator_labels():
    source = (ROOT / "examples" / "apply_high_win_credit_policy.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"NUMERIC_FEATURES", "FEATURES"}:
                    assigned[target.id] = ast.literal_eval(node.value) if target.id == "NUMERIC_FEATURES" else None
    features = assigned["NUMERIC_FEATURES"]
    forbidden = {"regime", "event_type", "mispricing_type", "injected_edge_active", "actual_remaining_return", "pnl", "profitable"}
    assert forbidden.isdisjoint(features)


def test_empirical_profile_is_declared():
    profile = ROOT / "config" / "empirical_occurrence_v1.json"
    assert profile.exists()
    text = profile.read_text(encoding="utf-8")
    assert "historically anchored" in text.lower()
    assert "not a substitute" in text.lower()


def test_balanced_policy_excludes_future_and_hidden_features():
    source = (ROOT / "examples" / "apply_balanced_expectancy_policy.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    numeric_features = None
    forbidden = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "NUMERIC_FEATURES":
                    numeric_features = ast.literal_eval(node.value)
                if isinstance(target, ast.Name) and target.id == "FORBIDDEN_FEATURES":
                    forbidden = ast.literal_eval(node.value)
    assert numeric_features is not None
    assert forbidden is not None
    assert set(numeric_features).isdisjoint(forbidden)
    assert "return_on_risk" not in numeric_features


def test_balanced_policy_locks_both_win_and_profit_factor_floors():
    source = (ROOT / "examples" / "apply_balanced_expectancy_policy.py").read_text(encoding="utf-8")
    assert '"target_win_rate_floor": 0.65' in source
    assert '"target_profit_factor_floor": 1.35' in source
    assert '"minimum_predicted_profit_factor": 1.50' in source

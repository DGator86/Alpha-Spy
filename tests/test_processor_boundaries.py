from __future__ import annotations

import ast
from pathlib import Path

PROCESSOR_ROOT = Path(__file__).parents[1] / "src" / "spy_platform"
FORBIDDEN_ALPHA_IMPORTS = {
    "alpha_spy.execution",
    "alpha_spy.risk",
    "alpha_spy.strategy",
    "alpha_spy.strategy_v2",
    "alpha_spy.position_management",
    "alpha_spy.broker_reconcile",
}
FORBIDDEN_BETA_IMPORTS = {
    "beta_spy.decision",
    "beta_spy.options",
    "beta_spy.ledger",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_processor_package_has_no_execution_or_legacy_trade_imports():
    violations: list[str] = []
    for path in PROCESSOR_ROOT.glob("*.py"):
        imported = _imports(path)
        forbidden = sorted(
            module
            for module in FORBIDDEN_ALPHA_IMPORTS | FORBIDDEN_BETA_IMPORTS
            if module in imported
        )
        if forbidden:
            violations.append(f"{path.name}: {', '.join(forbidden)}")
    assert not violations, "processor execution boundary violated: " + "; ".join(violations)


def test_processor_contracts_keep_alpha_beta_gamma_as_distinct_types():
    source = (PROCESSOR_ROOT / "contracts.py").read_text()
    assert "class AlphaState" in source
    assert "class BetaState" in source
    assert "class GammaState" in source
    assert "class DeltaState" in source
    assert "compiler_only_no_trade_authority" in source

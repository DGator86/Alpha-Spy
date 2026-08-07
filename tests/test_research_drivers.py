"""Execute each research driver end to end at minimal size.

These drivers ship in the release and are documented as runnable, but nothing
exercised them, so a missing `tabulate` dependency went unnoticed: every driver
ran its full simulation and then died in the report-writing step. Running them
here at two worlds costs a couple of seconds each and closes that gap for the
whole class of failure -- undeclared dependencies, import errors, and crashes
that only appear after the simulation finishes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"

# Drivers that accept --worlds/--output-dir and write a markdown report.
WORLD_DRIVERS = [
    "run_1000_world_1m_day",
    "run_full_strategy_tournament",
    "run_empirical_stress_tournament",
    "run_promoted_1000_world_1m_day",
]

pytest.importorskip(
    "tabulate",
    reason="research drivers need the [research] extra: pip install -e '.[research]'",
)


def _run(script: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=900,
    )


@pytest.mark.parametrize("name", WORLD_DRIVERS)
def test_world_driver_runs_and_writes_a_report(name, tmp_path):
    output = tmp_path / name
    result = _run(
        EXAMPLES / f"{name}.py",
        ["--worlds", "2", "--seed", "7", "--output-dir", str(output)],
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"{name} exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}"
    )
    report = output / "REPORT.md"
    assert report.is_file(), f"{name} produced no REPORT.md in {output}"
    assert report.stat().st_size > 0, f"{name} wrote an empty REPORT.md"


def test_synthetic_demo_reproduces_its_committed_output():
    """Golden check on the committed synthetic_edge_output.csv.

    The demo is seeded, so its edge table is deterministic. It writes next to
    the script rather than to a chosen directory, so this restores the file
    afterwards and compares instead of leaving whatever it produced behind.
    A mismatch means the pricing, scanning or ranking path changed numerically.
    """
    golden = EXAMPLES / "synthetic_edge_output.csv"
    original = golden.read_bytes()
    try:
        result = _run(EXAMPLES / "run_synthetic_demo.py", [], cwd=REPO_ROOT)
        assert result.returncode == 0, (
            f"run_synthetic_demo exited {result.returncode}\n"
            f"--- stderr ---\n{result.stderr[-2000:]}"
        )
        assert "Saved:" in result.stdout
        produced = golden.read_bytes()
    finally:
        golden.write_bytes(original)
    assert produced == original, (
        "run_synthetic_demo no longer reproduces examples/synthetic_edge_output.csv; "
        "the pricing/scanning/ranking path changed numerically"
    )

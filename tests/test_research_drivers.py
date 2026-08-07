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


def test_synthetic_demo_reproduces_its_committed_output(tmp_path):
    """Golden check on the committed synthetic_edge_output.csv.

    The demo is seeded, so its edge table is reproducible. Comparison is
    numeric rather than byte-exact: repr of a float differs in the last digit
    or two between interpreter and BLAS versions, which says nothing about the
    pricing path. Structure is compared exactly; values to a tight tolerance.

    The demo writes next to the script rather than to a chosen directory, so
    the committed file is restored afterwards.
    """
    import pandas as pd

    golden = EXAMPLES / "synthetic_edge_output.csv"
    original = golden.read_bytes()
    try:
        result = _run(EXAMPLES / "run_synthetic_demo.py", [], cwd=REPO_ROOT)
        assert result.returncode == 0, (
            f"run_synthetic_demo exited {result.returncode}\n"
            f"--- stderr ---\n{result.stderr[-2000:]}"
        )
        assert "Saved:" in result.stdout
        produced_bytes = golden.read_bytes()
    finally:
        golden.write_bytes(original)

    produced_path = tmp_path / "produced.csv"
    produced_path.write_bytes(produced_bytes)
    produced = pd.read_csv(produced_path)
    expected = pd.read_csv(golden)

    assert list(produced.columns) == list(expected.columns), "edge table columns changed"
    assert len(produced) == len(expected), "number of surviving candidates changed"

    # Candidates with identical scores tie in the ranking sort, and different
    # pandas/numpy builds break that tie differently. The invariant is the set
    # of candidates and their values, not the order ties happen to land in.
    key = "symbol"
    produced = produced.sort_values(key, kind="stable").reset_index(drop=True)
    expected = expected.sort_values(key, kind="stable").reset_index(drop=True)
    assert list(produced[key]) == list(expected[key]), "the surviving candidate set changed"

    for column in expected.columns:
        if pd.api.types.is_numeric_dtype(expected[column]):
            pd.testing.assert_series_equal(
                produced[column], expected[column], check_exact=False, rtol=1e-6, atol=1e-9
            )
        else:
            pd.testing.assert_series_equal(produced[column], expected[column])

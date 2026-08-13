#!/usr/bin/env python3
"""Regenerate frontend/src/demo/snapshot.json.

The GitHub Pages preview is a real build of the workstation with the network
layer swapped for this committed snapshot, so the published page always shows
the current UI rather than a hand-maintained mock of an older one.

The timestamp is fixed so re-running this on an unchanged demo generator
produces an identical file and does not create diff noise.
"""
from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from alpha_spy.dashboard.db import Repository
from alpha_spy.dashboard.demo import base_state, seed_history

SNAPSHOT_AT = datetime(2026, 8, 12, 15, 17, tzinfo=UTC)
OUTPUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "demo" / "snapshot.json"


def build() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repository(Path(tmp) / "preview.sqlite")
        seed_history(repo, SNAPSHOT_AT)
        state = base_state(SNAPSHOT_AT, 7)
        state["predictions"] = repo.list_predictions(120)
        state["prediction_metrics"] = repo.prediction_metrics(500)
        state["alerts"] = repo.list_alerts(60)
        state["commands"] = repo.list_commands(30)
    state["tradier"] = {
        "configured": True,
        "environment": "sandbox",
        "quote": {"updated_at": state["timestamp"]},
        "account": {"updated_at": state["timestamp"]},
    }
    return state


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=0, sort_keys=True, default=str), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(Path.cwd())} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

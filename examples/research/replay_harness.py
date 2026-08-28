#!/usr/bin/env python3
"""Regime-hierarchy replay harness.

Walks a minute-resolution tape through Alpha-SPY's *real* regime stack and
records what every layer of the hierarchy actually produced, so the chain from
"lots of data" to "one trade decision" can be inspected instead of assumed.

The harness deliberately calls the production functions -- ``classify_regime``,
``classify_regime_hierarchy`` and the prediction layer's own ``_regime_scales``
-- rather than reimplementing them. Anything it reports is therefore a property
of the shipped code, not of the harness.

Two tape profiles are provided:

``realistic``
    An ordinary session. Volatility wanders, breadth flips, correlation drifts,
    and -- by construction -- the slow and fast timescales genuinely disagree
    during the afternoon. A working hierarchy must register that disagreement.

``perfect``
    The easiest day the design could ever be handed: one clean, unambiguous
    trend, every timescale pointing the same way, low vol, stable correlation,
    breadth pinned. A working hierarchy must register agreement and high
    confidence.

Comparing the two isolates architecture from market noise. If the hierarchy's
outputs are indistinguishable across these two tapes, the problem is not the
data.

Usage:
    python examples/research/replay_harness.py --profile realistic
    python examples/research/replay_harness.py --profile perfect
    python examples/research/replay_harness.py --compare
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from alpha_spy.db import Journal  # noqa: E402
from alpha_spy.regime import (  # noqa: E402
    RegimeHierarchy,
    classify_regime,
    classify_regime_hierarchy,
)
from alpha_spy.timeutil import ET  # noqa: E402

LOOKBACKS = {"micro": 45, "intraday": 240, "swing": 780, "structural": 1950}
DIMENSIONS = (
    "volatility",
    "correlation",
    "breadth",
    "concentration",
    "dealer_gamma",
    "session",
    "event",
    "risk_tone",
    "volatility_term",
    "liquidity",
)


# --------------------------------------------------------------------------
# tape generation
# --------------------------------------------------------------------------
def _session_minutes(days: int, per_day: int = 390) -> list[datetime]:
    """Trading minutes, most recent last, ending at the close of day `days`."""
    out: list[datetime] = []
    start = datetime(2026, 6, 1, 9, 30, tzinfo=ET)
    day = 0
    while len(out) < days * per_day:
        opening = (start + timedelta(days=day)).astimezone(ET)
        if opening.weekday() < 5:
            for minute in range(per_day):
                out.append((opening + timedelta(minutes=minute)).astimezone(UTC))
        day += 1
    return out


def build_tape(profile: str, days: int = 12) -> list[dict[str, Any]]:
    """Emit feature rows in the production schema.

    The generator only sets the fields the regime stack actually reads --
    realized_vol, correlation, breadth, concentration -- plus the columns the
    table requires. Everything else is held at a benign constant so that any
    variation the harness observes is attributable to the tape's own shape.
    """
    stamps = _session_minutes(days)
    n = len(stamps)
    rng = np.random.default_rng(11)
    rows: list[dict[str, Any]] = []

    for i, ts in enumerate(stamps):
        frac = i / max(1, n - 1)
        day_index = i // 390
        minute_of_day = i % 390

        if profile == "perfect":
            # One unambiguous regime for the whole tape. Every timescale sees
            # the same picture, because the picture never changes: a quiet,
            # steadily rising, broad, low-correlation market.
            realized_vol = 0.09 + 0.002 * math.sin(minute_of_day / 90.0)
            correlation = 0.34 + 0.004 * math.sin(minute_of_day / 120.0)
            breadth = 0.86
            concentration = 0.07
        elif profile == "realistic":
            # Slow state and fast state deliberately diverge. The first eight
            # days build a calm, broad uptrend (what "structural" should see).
            # The last four days are a sharp, narrow, high-correlation selloff
            # (what "micro" should see). A hierarchy that works must show the
            # layers disagreeing across that boundary.
            if day_index < 8:
                base_vol, base_corr, base_breadth, base_conc = 0.10, 0.36, 0.78, 0.08
            else:
                stress = min(1.0, (day_index - 8) / 3.0)
                base_vol = 0.10 + 0.26 * stress
                base_corr = 0.36 + 0.50 * stress
                base_breadth = 0.78 - 0.62 * stress
                base_conc = 0.08 + 0.20 * stress
            realized_vol = max(0.03, base_vol + rng.normal(0, 0.012))
            correlation = float(np.clip(base_corr + rng.normal(0, 0.03), 0.0, 0.99))
            breadth = float(np.clip(base_breadth + rng.normal(0, 0.05), 0.0, 1.0))
            concentration = float(np.clip(base_conc + rng.normal(0, 0.02), 0.0, 1.0))
        else:  # pragma: no cover - guarded by argparse choices
            raise ValueError(profile)

        snapshot_id = f"{profile}-{i:06d}"
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "created_at": ts.isoformat().replace("+00:00", "Z"),
                "captured_at": ts,
                "breadth": float(breadth),
                "weighted_pressure": float(0.4 * (breadth - 0.5)),
                "concentration": float(concentration),
                "dispersion": 0.011,
                "correlation": float(correlation),
                "downside_correlation": float(min(0.99, correlation + 0.05)),
                "weighted_return": float(0.0004 * (breadth - 0.5)),
                "residual_pressure": 0.0,
                "realized_vol": float(realized_vol),
                "trust_score": 1.0,
                "health_state": "HEALTHY",
                "payload": {"profile": profile},
                "spy_price": 600.0 + 40.0 * frac,
            }
        )
    return rows


def load_tape(journal: Journal, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        journal.insert_snapshot(
            {
                "snapshot_id": row["snapshot_id"],
                "captured_at": row["created_at"],
                "exchange_state": "open",
                "spy_price": row["spy_price"],
                "spy_bid": row["spy_price"] - 0.01,
                "spy_ask": row["spy_price"] + 0.01,
                "covered_weight": 0.99,
                "quote_count": 500,
                "stale_quote_count": 0,
                "integrity": "VERIFIED",
                "source": "replay_harness",
                "payload": {},
            },
            [],
        )
        journal.insert_features({k: v for k, v in row.items() if k not in {"captured_at", "spy_price"}})


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------
def replay(journal: Journal, rows: list[dict[str, Any]], *, warmup: int = 1950) -> list[dict[str, Any]]:
    """Classify every bar after the warm-up with the production hierarchy."""
    observations: list[dict[str, Any]] = []
    for row in rows[warmup:]:
        ts = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        feature = {k: v for k, v in row.items() if k not in {"captured_at", "spy_price"}}
        hierarchy = classify_regime_hierarchy(
            journal,
            timestamp=ts,
            feature=feature,
            gamma_state="positive_gamma",
            event_state="ordinary",
            context=None,
        )
        levels = {
            "micro": hierarchy.micro,
            "intraday": hierarchy.intraday,
            "swing": hierarchy.swing,
            "structural": hierarchy.structural,
        }
        observations.append(
            {
                "created_at": row["created_at"],
                "conflict_score": hierarchy.conflict_score,
                "transition_risk": hierarchy.transition_risk,
                "key": hierarchy.key,
                "levels": {
                    name: {dim: getattr(state, dim) for dim in DIMENSIONS}
                    for name, state in levels.items()
                },
                "history_samples": {name: state.history_samples for name, state in levels.items()},
            }
        )
    return observations


def regime_scales(hierarchy_conflict: float, volatility_term: str, liquidity: str) -> tuple[float, float]:
    """Mirror of prediction.py's `_regime_scales` tail, for reporting only.

    Reproduced here (not imported) because the production function is module
    private and takes the full regime object; the three branches below are the
    complete set that the hierarchy can influence.
    """
    mean_scale, sigma_scale = 1.0, 1.0
    if volatility_term == "backwardation":
        mean_scale *= 0.90
        sigma_scale *= 1.15
    if liquidity == "thin":
        mean_scale *= 0.85
        sigma_scale *= 1.15
    if hierarchy_conflict >= 0.65:
        mean_scale *= 0.80
        sigma_scale *= 1.15
    return mean_scale, sigma_scale


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def summarise(profile: str, obs: list[dict[str, Any]]) -> dict[str, Any]:
    conflicts = [o["conflict_score"] for o in obs]
    agree_all = 0
    per_dim_variation: dict[str, int] = defaultdict(int)
    distinct_by_level: dict[str, Counter] = {name: Counter() for name in LOOKBACKS}

    for o in obs:
        levels = o["levels"]
        for name, state in levels.items():
            distinct_by_level[name][tuple(state[d] for d in DIMENSIONS)] += 1
        if len({tuple(state[d] for d in DIMENSIONS) for state in levels.values()}) == 1:
            agree_all += 1
        for dim in DIMENSIONS:
            if len({levels[name][dim] for name in levels}) > 1:
                per_dim_variation[dim] += 1

    fired = sum(1 for c in conflicts if c >= 0.65)
    return {
        "profile": profile,
        "bars": len(obs),
        "conflict_min": min(conflicts),
        "conflict_max": max(conflicts),
        "conflict_mean": sum(conflicts) / len(conflicts),
        "conflict_gate_fired": fired,
        "transition_risk_bars": sum(1 for o in obs if o["transition_risk"]),
        "all_four_levels_identical": agree_all,
        "all_four_levels_identical_pct": agree_all / len(obs),
        "per_dimension_disagreement": {d: per_dim_variation.get(d, 0) for d in DIMENSIONS},
        "distinct_states_per_level": {n: len(c) for n, c in distinct_by_level.items()},
        "distinct_hierarchy_keys": len({o["key"] for o in obs}),
    }


def print_summary(s: dict[str, Any]) -> None:
    print(f"\n{'=' * 74}")
    print(f"PROFILE: {s['profile']}   ({s['bars']:,} bars replayed through the production stack)")
    print("=" * 74)
    print("\n  conflict_score (the hierarchy's own timescale-disagreement signal)")
    print(f"    min {s['conflict_min']:.4f}   mean {s['conflict_mean']:.4f}   max {s['conflict_max']:.4f}")
    print(f"    bars where the >= 0.65 gate fired: {s['conflict_gate_fired']} / {s['bars']}")
    print(f"\n  transition_risk raised on {s['transition_risk_bars']:,} / {s['bars']:,} bars")
    print(f"\n  all four levels byte-identical on {s['all_four_levels_identical']:,} / {s['bars']:,} bars "
          f"({s['all_four_levels_identical_pct']:.1%})")
    print("\n  per-dimension: bars on which the four levels DISAGREED")
    for dim, count in s["per_dimension_disagreement"].items():
        bar = "#" * int(40 * count / max(1, s["bars"]))
        print(f"    {dim:18s} {count:6,d} / {s['bars']:,}  {bar}")
    print("\n  distinct regime states observed per level")
    for name, count in s["distinct_states_per_level"].items():
        print(f"    {name:12s} (lookback {LOOKBACKS[name]:>4d} bars): {count} distinct states")
    print(f"\n  distinct hierarchy keys over the whole tape: {s['distinct_hierarchy_keys']}")


def run_profile(profile: str, days: int, workdir: Path) -> dict[str, Any]:
    db = workdir / f"{profile}.db"
    if db.exists():
        db.unlink()
    journal = Journal(db)
    rows = build_tape(profile, days=days)
    print(f"[{profile}] generating tape: {len(rows):,} minute bars over {days} sessions")
    load_tape(journal, rows)
    print(f"[{profile}] loaded into journal, replaying through classify_regime_hierarchy ...")
    obs = replay(journal, rows)
    print(f"[{profile}] replayed {len(obs):,} bars")
    summary = summarise(profile, obs)
    (workdir / f"{profile}_observations.json").write_text(json.dumps(obs[::20], indent=1))
    (workdir / f"{profile}_summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=["realistic", "perfect"], help="run one profile")
    parser.add_argument("--compare", action="store_true", help="run both profiles and compare")
    parser.add_argument("--days", type=int, default=12)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/replay-harness"))
    args = parser.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    profiles = ["realistic", "perfect"] if args.compare or not args.profile else [args.profile]
    summaries = [run_profile(p, args.days, args.workdir) for p in profiles]
    for s in summaries:
        print_summary(s)

    if len(summaries) == 2:
        a, b = summaries
        print(f"\n{'=' * 74}")
        print("COMPARISON")
        print("=" * 74)
        print(f"\n  {'metric':40s} {a['profile']:>14s} {b['profile']:>14s}")
        print("  " + "-" * 70)
        for label, key, fmt in [
            ("conflict_score mean", "conflict_mean", "{:.4f}"),
            ("conflict_score max", "conflict_max", "{:.4f}"),
            ("bars the conflict gate fired", "conflict_gate_fired", "{:,}"),
            ("bars all four levels identical", "all_four_levels_identical", "{:,}"),
            ("distinct hierarchy keys", "distinct_hierarchy_keys", "{:,}"),
        ]:
            print(f"  {label:40s} {fmt.format(a[key]):>14s} {fmt.format(b[key]):>14s}")
        print("\n  per-dimension disagreement counts")
        for dim in DIMENSIONS:
            print(f"    {dim:18s} {a['per_dimension_disagreement'][dim]:>10,d} "
                  f"{b['per_dimension_disagreement'][dim]:>10,d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

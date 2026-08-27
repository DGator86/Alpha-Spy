from __future__ import annotations

import json
from typing import Any

from .regime import RegimeHierarchy, RegimeState
from .v2_lifecycle import AlphaRegimeLifecycleEngine


def _state(raw: Any) -> RegimeState | None:
    if not isinstance(raw, dict):
        return None
    required = {
        "volatility",
        "correlation",
        "breadth",
        "concentration",
        "dealer_gamma",
        "session",
        "event",
        "transition_risk",
        "history_samples",
    }
    if not required.issubset(raw):
        return None
    try:
        return RegimeState(
            volatility=str(raw["volatility"]),
            correlation=str(raw["correlation"]),
            breadth=str(raw["breadth"]),
            concentration=str(raw["concentration"]),
            dealer_gamma=str(raw["dealer_gamma"]),
            session=str(raw["session"]),
            event=str(raw["event"]),
            transition_risk=bool(raw["transition_risk"]),
            history_samples=int(raw["history_samples"]),
            risk_tone=str(raw.get("risk_tone") or "neutral"),
            volatility_term=str(raw.get("volatility_term") or "unknown"),
            liquidity=str(raw.get("liquidity") or "normal"),
        )
    except (TypeError, ValueError):
        return None


def hierarchy_from_payload(payload: dict[str, Any]) -> RegimeHierarchy | None:
    raw = payload.get("regime_hierarchy") or payload.get("alpha_regime")
    if not isinstance(raw, dict):
        return None
    micro = _state(raw.get("micro"))
    intraday = _state(raw.get("intraday"))
    swing = _state(raw.get("swing"))
    structural = _state(raw.get("structural"))
    if None in {micro, intraday, swing, structural}:
        return None
    try:
        return RegimeHierarchy(
            micro=micro,
            intraday=intraday,
            swing=swing,
            structural=structural,
            conflict_score=float(raw.get("conflict_score") or 0.0),
            transition_risk=bool(raw.get("transition_risk")),
        )
    except (TypeError, ValueError):
        return None


def backfill_lifecycle_from_frozen_predictions(journal, *, limit: int = 25000) -> dict[str, Any]:
    """Populate lifecycle observations from Alpha regimes frozen at decision time.

    This does not reconstruct or relabel history with today's model. It reads the
    exact `regime_hierarchy` payload already stored with historical Alpha predictions
    and inserts one observation per frozen snapshot. Beta witness values are left
    empty because retroactively fabricating them would violate witness independence.
    """
    engine = AlphaRegimeLifecycleEngine(journal)
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT snapshot_id,created_at,payload_json
            FROM predictions
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    seen: set[str] = set()
    inserted = 0
    skipped_duplicate = 0
    missing_hierarchy = 0
    invalid_payload = 0
    for row in rows:
        snapshot_id = str(row["snapshot_id"])
        if snapshot_id in seen:
            skipped_duplicate += 1
            continue
        seen.add(snapshot_id)
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            invalid_payload += 1
            continue
        if not isinstance(payload, dict):
            invalid_payload += 1
            continue
        hierarchy = hierarchy_from_payload(payload)
        if hierarchy is None:
            missing_hierarchy += 1
            continue
        before = len(engine._rows(limit=1_000_000))
        engine.record_observation(
            snapshot_id=snapshot_id,
            captured_at=str(row["created_at"]),
            hierarchy=hierarchy,
            beta=None,
        )
        after = len(engine._rows(limit=1_000_000))
        inserted += int(after > before)

    observations = engine._rows(limit=1_000_000)
    episodes = engine._episodes(observations)
    completed = sum(bool(episode.get("completed")) for episode in episodes)
    regime_counts: dict[str, int] = {}
    for episode in episodes:
        regime = str(episode.get("regime") or "UNDEFINED")
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
    return {
        "predictions_scanned": len(rows),
        "unique_snapshots_seen": len(seen),
        "observations_inserted": inserted,
        "duplicate_horizons_skipped": skipped_duplicate,
        "missing_hierarchy": missing_hierarchy,
        "invalid_payload": invalid_payload,
        "total_lifecycle_observations": len(observations),
        "regime_episodes": len(episodes),
        "completed_regime_episodes": completed,
        "regime_episode_counts": regime_counts,
        "source": "frozen_alpha_prediction_regime_hierarchy",
        "beta_retrofit": False,
    }

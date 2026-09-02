from datetime import UTC, datetime, timedelta

from alpha_spy.v2_pending_entry import resolve_pending_entry


def _regime(current="QUIET", *, p15=0.70, p30=0.80, successor="QUIET", successor_conf=0.60):
    return {
        "regime_forecast": {
            "definable": True,
            "current_regime": current,
            "confidence": 0.80,
            "persistence_15": p15,
            "persistence_30": p30,
            "most_likely_successor": successor,
            "successor_confidence": successor_conf,
        },
        "hgb_direction": {"eligible": False},
    }


def _thesis(now, *, mode, regime="QUIET", successor="QUIET", playbook="LATE_RANGE_CARRY", direction="NEUTRAL"):
    return {
        "thesis_id": "T-1",
        "regime": regime,
        "most_likely_successor": successor,
        "entry_mode": mode,
        "playbook": playbook,
        "direction": direction,
        "setup_expires_at": (now + timedelta(minutes=20)).isoformat(),
    }


def test_pending_range_waits_then_releases_in_late_window():
    early = datetime(2026, 8, 27, 18, 35, tzinfo=UTC)  # 14:35 ET
    thesis = _thesis(early, mode="WAIT_FOR_BETTER_PRICING")
    waiting = resolve_pending_entry(thesis, _regime(), now=early)
    assert waiting.action == "WAIT"

    late = datetime(2026, 8, 27, 18, 50, tzinfo=UTC)
    released = resolve_pending_entry(thesis, _regime(), now=late)
    assert released.action == "RELEASE"


def test_pending_transition_releases_only_when_successor_arrives():
    now = datetime(2026, 8, 27, 16, 0, tzinfo=UTC)
    thesis = _thesis(
        now,
        mode="WAIT_FOR_TRANSITION",
        regime="TRANSITION",
        successor="DIRECTIONAL_DOWN",
        playbook="REGIME_TRANSITION",
        direction="BEARISH",
    )
    waiting = resolve_pending_entry(
        thesis,
        _regime("TRANSITION", successor="DIRECTIONAL_DOWN"),
        now=now,
    )
    assert waiting.action == "WAIT"

    arrived = resolve_pending_entry(
        thesis,
        _regime("DIRECTIONAL_DOWN", successor="QUIET"),
        now=now + timedelta(minutes=5),
    )
    assert arrived.action == "RELEASE"


def test_pending_setup_expires_and_is_abandoned():
    now = datetime(2026, 8, 27, 16, 0, tzinfo=UTC)
    thesis = _thesis(now, mode="WAIT_FOR_CONFIRMATION")
    expired = dict(thesis)
    expired["setup_expires_at"] = (now - timedelta(seconds=1)).isoformat()
    result = resolve_pending_entry(expired, _regime(), now=now)
    assert result.action == "CANCEL"
    assert result.reason == "pending_thesis_expired"

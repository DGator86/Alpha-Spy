from datetime import UTC, datetime, timedelta

from alpha_spy.v2_runtime_repaired import evidence_provenance


def _snapshot(at: datetime, *, source: str = "tradier_production_stream", integrity: str = "VERIFIED"):
    return {
        "snapshot_id": "S-forward",
        "captured_at": at.isoformat(),
        "source": source,
        "integrity": integrity,
    }


def _chain(at: datetime, *, source: str = "tradier", integrity: str = "VERIFIED"):
    return {
        "chain_snapshot_id": "OC-forward",
        "captured_at": at.isoformat(),
        "source": source,
        "integrity": integrity,
    }


def test_fresh_verified_production_snapshot_and_chain_are_forward_evidence():
    now = datetime(2026, 9, 2, 14, 30, tzinfo=UTC)
    result = evidence_provenance(
        _snapshot(now - timedelta(seconds=20)),
        _chain(now - timedelta(seconds=15)),
        now=now,
    )
    assert result["evidence_class"] == "FORWARD_ACTUAL_CHAIN"
    assert result["actual_chain"] is True


def test_replay_or_stale_chain_can_never_be_forward_evidence():
    now = datetime(2026, 9, 2, 14, 30, tzinfo=UTC)
    replay = evidence_provenance(
        _snapshot(now - timedelta(seconds=20), source="replay_harness"),
        _chain(now - timedelta(seconds=15)),
        now=now,
    )
    assert replay["evidence_class"] == "REPLAY_OR_UNVERIFIED"
    assert replay["actual_chain"] is False

    stale = evidence_provenance(
        _snapshot(now - timedelta(minutes=10)),
        _chain(now - timedelta(minutes=10)),
        now=now,
    )
    assert stale["evidence_class"] == "REPLAY_OR_UNVERIFIED"
    assert stale["actual_chain"] is False


def test_unverified_chain_can_never_promote_itself():
    now = datetime(2026, 9, 2, 14, 30, tzinfo=UTC)
    result = evidence_provenance(
        _snapshot(now - timedelta(seconds=20)),
        _chain(now - timedelta(seconds=15), integrity="INCOMPLETE"),
        now=now,
    )
    assert result["evidence_class"] == "REPLAY_OR_UNVERIFIED"
    assert result["actual_chain"] is False

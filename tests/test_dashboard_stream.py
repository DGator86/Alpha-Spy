"""Tests for the sectioned dashboard websocket protocol.

The socket used to resend the whole `build_state()` payload on every tick, which
meant a SPY quote change dragged 120 predictions, 60 alerts and the promotion
evidence across the wire behind it. It now sends an opening snapshot and then
only the sections that actually changed. These pin the properties a client
depends on: a snapshot is complete, an unchanged state produces no frame, and a
changed section carries its whole body rather than a partial patch.
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime

from alpha_spy.dashboard.demo import base_state
from alpha_spy.dashboard.service import SECTIONS, SectionStream, split_sections


def _state(tick: int = 0) -> dict:
    return base_state(datetime(2026, 8, 12, 15, 0, tzinfo=UTC), tick)


def test_every_published_key_is_routed_to_a_section():
    state = _state()
    routed = {key for keys in SECTIONS.values() for key in keys}
    unrouted = set(state) - routed
    # `timestamp` rides on the frame envelope rather than inside a section.
    assert unrouted == {"timestamp"}, f"unrouted state keys would never reach the client: {unrouted}"


def test_snapshot_carries_every_section():
    stream = SectionStream()
    state = _state()
    frame = stream.snapshot(state)

    assert frame["type"] == "snapshot"
    assert frame["seq"] == 1
    assert set(frame["sections"]) == set(split_sections(state))
    # Merging the sections back together must reproduce the state exactly, or a
    # client that only ever sees sections is looking at a lossy view.
    merged: dict = {}
    for body in frame["sections"].values():
        merged.update(body)
    assert merged == {k: v for k, v in state.items() if k != "timestamp"}


def test_unchanged_state_produces_no_patch():
    stream = SectionStream()
    state = _state()
    stream.snapshot(state)
    assert stream.patch(copy.deepcopy(state)) is None
    # A suppressed frame must not burn a sequence number, otherwise a client
    # watching for gaps would see one on every quiet tick.
    assert stream.seq == 1


def test_patch_carries_only_changed_sections():
    stream = SectionStream()
    state = _state()
    stream.snapshot(state)

    changed = copy.deepcopy(state)
    changed["market"] = {**changed["market"], "price": 999.99}
    frame = stream.patch(changed)

    assert frame is not None
    assert frame["type"] == "patch"
    assert frame["seq"] == 2
    assert set(frame["sections"]) == {"market"}
    assert frame["sections"]["market"]["market"]["price"] == 999.99
    assert frame["removed"] == []


def test_patch_sends_the_whole_section_not_a_field_diff():
    stream = SectionStream()
    state = _state()
    stream.snapshot(state)

    changed = copy.deepcopy(state)
    changed["market"] = {**changed["market"], "price": 1.0}
    frame = stream.patch(changed)

    assert frame is not None
    # Clients replace a section wholesale. A field-level diff would leave stale
    # keys alive after the engine stopped publishing them.
    assert frame["sections"]["market"]["market"] == changed["market"]


def test_dropped_section_is_reported_as_removed():
    stream = SectionStream()
    state = _state()
    stream.snapshot(state)

    reduced = copy.deepcopy(state)
    del reduced["candidates"]
    frame = stream.patch(reduced)

    assert frame is not None
    assert "candidates" in frame["removed"]
    # Removing it again is not a change, so the stream goes quiet.
    assert stream.patch(copy.deepcopy(reduced)) is None


def test_each_connection_gets_its_own_opening_snapshot():
    state = _state()
    established = SectionStream()
    established.snapshot(state)
    assert established.patch(copy.deepcopy(state)) is None

    # A client joining mid-session must not inherit another connection's
    # digests, or it would start from an empty state and never be sent one.
    joiner = SectionStream()
    frame = joiner.snapshot(state)
    assert set(frame["sections"]) == set(split_sections(state))


def test_high_cardinality_sections_stay_quiet_across_ticks():
    """The whole reason the protocol changed.

    Ticking the demo state moves prices, forecasts and the candidate book. The
    sections that do not move — validation evidence, security posture, the
    service roster — must not be retransmitted.
    """
    stream = SectionStream()
    stream.snapshot(_state(0))
    resent: set[str] = set()
    for tick in range(1, 8):
        frame = stream.patch(_state(tick))
        if frame:
            resent |= set(frame["sections"])
    assert "validation" not in resent
    assert "security" not in resent
    assert "engine" not in resent
    # ...while the genuinely live sections do move.
    assert "market" in resent

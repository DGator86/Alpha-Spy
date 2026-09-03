from __future__ import annotations

from datetime import UTC, datetime

from spy_platform.ingestion import normalize_tradier_message
from spy_platform.market_store import MarketEventStore
from spy_platform.raw_market import MarketFrame


def test_shared_store_deduplicates_replayed_timesale_sequence(tmp_path):
    message = {
        "type": "timesale",
        "symbol": "SPY",
        "date": 1788446520000,
        "seq": 12345,
        "price": 652.25,
        "size": 100,
    }
    event = normalize_tradier_message(
        message,
        received_at=datetime(2026, 9, 3, 14, 42, 0, 50000, tzinfo=UTC),
    )
    assert event is not None
    store = MarketEventStore(tmp_path / "market.sqlite")
    assert store.append(event) is True
    assert store.append(event) is False
    assert store.count_events() == 1


def test_models_can_query_same_canonical_event_history(tmp_path):
    store = MarketEventStore(tmp_path / "market.sqlite")
    quote = normalize_tradier_message(
        {
            "type": "quote",
            "symbol": "SPY",
            "timestamp": "2026-09-03T14:42:00Z",
            "bid": 652.20,
            "ask": 652.22,
        },
        received_at=datetime(2026, 9, 3, 14, 42, 0, 100000, tzinfo=UTC),
    )
    assert quote is not None
    store.append(quote)
    alpha_view = store.events_until(
        as_of="2026-09-03T14:42:01Z",
        symbols={"SPY"},
        event_types={"QUOTE"},
    )
    beta_view = store.events_until(
        as_of="2026-09-03T14:42:01Z",
        symbols={"SPY"},
        event_types={"QUOTE"},
    )
    assert [event.event_id for event in alpha_view] == [event.event_id for event in beta_view]


def test_frame_references_shared_event_ids(tmp_path):
    store = MarketEventStore(tmp_path / "market.sqlite")
    event = normalize_tradier_message(
        {
            "type": "trade",
            "symbol": "SPY",
            "timestamp": "2026-09-03T14:42:00Z",
            "price": 652.21,
            "size": 50,
        },
        received_at=datetime(2026, 9, 3, 14, 42, 0, 100000, tzinfo=UTC),
    )
    assert event is not None
    store.append(event)
    frame = MarketFrame.from_events(as_of="2026-09-03T14:42:00Z", events=[event])
    assert store.record_frame(frame) is True
    assert store.record_frame(frame) is False

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alpha_spy.beta_opportunity import (
    BLIND_V1_CONFIG_SHA256,
    attach_beta_opportunity,
    opportunity_from_beta_state,
    opportunity_is_current,
)


def _payload(timestamp: datetime) -> dict:
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "eligible": True,
        "direction_prior": "DOWN",
        "probability_up": 0.28,
        "expected_return_bps": -6.4,
        "supporting_horizons": 1,
        "breadth_5": 0.40,
        "reasons": [],
        "config_sha256": BLIND_V1_CONFIG_SHA256,
    }


def test_reads_strategy_agnostic_beta_state_and_attaches_context() -> None:
    now = datetime(2026, 8, 18, 15, 25, tzinfo=UTC)
    opportunity = opportunity_from_beta_state({"opportunity": _payload(now)})
    assert opportunity is not None
    assert opportunity.direction_prior == "DOWN"
    assert opportunity_is_current(opportunity, as_of=now + timedelta(seconds=30))

    prediction = {"prediction_id": "P-1", "payload": {}}
    attached = attach_beta_opportunity(prediction, opportunity)
    beta = attached["payload"]["beta_opportunity"]
    assert beta["strategy_authority"] is False
    assert beta["direction_prior"] == "DOWN"
    assert prediction["payload"] == {}


def test_rejects_stale_or_wrong_config_signal() -> None:
    now = datetime(2026, 8, 18, 15, 25, tzinfo=UTC)
    stale = opportunity_from_beta_state({"opportunity": _payload(now - timedelta(minutes=5))})
    assert stale is not None
    assert not opportunity_is_current(stale, as_of=now)

    wrong = _payload(now)
    wrong["config_sha256"] = "not-the-frozen-config"
    opportunity = opportunity_from_beta_state({"opportunity": wrong})
    assert opportunity is not None
    assert not opportunity_is_current(opportunity, as_of=now)

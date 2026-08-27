from datetime import UTC, datetime, timedelta

from alpha_spy.db import Journal
from alpha_spy.regime import RegimeHierarchy, RegimeState
from alpha_spy.v2_lifecycle import AlphaRegimeLifecycleEngine, canonical_alpha_regime


def _state(*, breadth="mixed", volatility="normal", risk_tone="neutral"):
    return RegimeState(
        volatility=volatility,
        correlation="stable",
        breadth=breadth,
        concentration="distributed",
        dealer_gamma="positive_gamma",
        session="midday",
        event="ordinary",
        transition_risk=False,
        history_samples=100,
        risk_tone=risk_tone,
        volatility_term="contango",
        liquidity="normal",
    )


def _hierarchy(*, breadth="mixed", volatility="normal", risk_tone="neutral", conflict=0.05):
    state = _state(breadth=breadth, volatility=volatility, risk_tone=risk_tone)
    return RegimeHierarchy(
        micro=state,
        intraday=state,
        swing=state,
        structural=state,
        conflict_score=conflict,
        transition_risk=False,
    )


def _beta(direction="BULLISH"):
    return {
        "hgb_direction": {"eligible": True, "direction": direction, "strength": 0.70},
        "predictive_state": {
            "ready": True,
            "p_big_15": 0.20,
            "p_big_30": 0.25,
            "p_reversal_15": 0.20,
            "p_persistent_30": 0.70,
        },
        "regime_forecast": {
            "definable": True,
            "current_regime": "DIRECTIONAL_DOWN",
            "confidence": 0.99,
        },
    }


def test_alpha_hierarchy_owns_current_regime_even_when_beta_disagrees():
    hierarchy = _hierarchy(breadth="broad_up", risk_tone="risk_on")
    assert canonical_alpha_regime(hierarchy) == "DIRECTIONAL_UP"


def test_lifecycle_starts_provisional_without_completed_alpha_episodes(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    engine = AlphaRegimeLifecycleEngine(journal)
    stamp = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
    forecast = engine.forecast(
        snapshot_id="S-now",
        captured_at=stamp.isoformat(),
        hierarchy=_hierarchy(),
        beta=_beta("BEARISH"),
    )
    assert forecast.current_regime == "QUIET"
    assert forecast.source == "PROVISIONAL_ALPHA_PLUS_BETA_WITNESS_FALLBACK"
    assert forecast.definable is False
    assert forecast.beta_witness["direction"] == "BEARISH"


def test_completed_alpha_episodes_drive_empirical_survival_and_successor(tmp_path):
    journal = Journal(tmp_path / "alpha.db")
    engine = AlphaRegimeLifecycleEngine(journal)
    start = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)
    quiet = _hierarchy()
    bullish = _hierarchy(breadth="broad_up", risk_tone="risk_on")

    # Twelve independent historical sessions. Quiet survives ten minutes and
    # then transitions to Alpha DIRECTIONAL_UP. Beta deliberately says bearish;
    # successor learning must still come from the observed Alpha episodes.
    for day in range(12):
        base = start + timedelta(days=day)
        engine.record_observation(
            snapshot_id=f"Q-{day}-0",
            captured_at=base.isoformat(),
            hierarchy=quiet,
            beta=_beta("BEARISH"),
        )
        engine.record_observation(
            snapshot_id=f"Q-{day}-5",
            captured_at=(base + timedelta(minutes=5)).isoformat(),
            hierarchy=quiet,
            beta=_beta("BEARISH"),
        )
        engine.record_observation(
            snapshot_id=f"U-{day}-10",
            captured_at=(base + timedelta(minutes=10)).isoformat(),
            hierarchy=bullish,
            beta=_beta("BEARISH"),
        )

    now = start + timedelta(days=20)
    forecast = engine.forecast(
        snapshot_id="Q-current",
        captured_at=now.isoformat(),
        hierarchy=quiet,
        beta=_beta("BEARISH"),
    )

    assert forecast.current_regime == "QUIET"
    assert forecast.source == "EMPIRICAL_ALPHA_EPISODE_SURVIVAL"
    assert forecast.definable is True
    assert forecast.matched_episodes >= 10
    assert forecast.persistence_5 > 0.95
    assert forecast.persistence_15 < 0.05
    assert forecast.most_likely_successor == "DIRECTIONAL_UP"
    assert forecast.successor_probabilities["DIRECTIONAL_UP"] > 0.90

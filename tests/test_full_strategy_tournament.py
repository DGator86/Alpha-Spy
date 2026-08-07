from examples.run_full_strategy_tournament import build_strategy_candidates


def test_full_strategy_universe_is_defined_risk() -> None:
    candidates = build_strategy_candidates(
        spot=500.0,
        tenor=180.0 / (252 * 390),
        market_iv=0.22,
        market_skew=0.25,
        model_q_iv=0.24,
        model_q_skew=0.30,
        model_p_iv=0.20,
        model_p_drift_annual=0.10,
        forecast_remaining_return=0.004,
    )
    assert candidates
    assert all(candidate.max_loss >= 0.0 for candidate in candidates)
    assert all(sum(qty for right, _, qty in candidate.legs if right == "C") >= 0 for candidate in candidates)


def test_premium_selling_structures_are_present_when_vol_is_overpriced() -> None:
    candidates = build_strategy_candidates(
        spot=500.0,
        tenor=180.0 / (252 * 390),
        market_iv=0.28,
        market_skew=0.25,
        model_q_iv=0.22,
        model_q_skew=0.25,
        model_p_iv=0.18,
        model_p_drift_annual=-0.05,
        forecast_remaining_return=-0.001,
    )
    names = {candidate.name for candidate in candidates}
    assert "BEAR_CALL_CREDIT_SPREAD" in names
    assert "IRON_CONDOR" in names
    assert "IRON_BUTTERFLY" in names
    assert any(name.startswith("ADAPTIVE_IRON_CONDOR") for name in names)


def test_adaptive_condors_include_tail_metrics_and_defined_management() -> None:
    candidates = build_strategy_candidates(
        spot=500.0,
        tenor=150.0 / (252 * 390),
        market_iv=0.28,
        market_skew=0.25,
        model_q_iv=0.22,
        model_q_skew=0.24,
        model_p_iv=0.16,
        model_p_drift_annual=0.0,
        forecast_remaining_return=0.0002,
        entry_minute=240,
        breadth_5=0.52,
        concentration_5=0.55,
    )
    adaptive = [candidate for candidate in candidates if candidate.name.startswith("ADAPTIVE_IRON_CONDOR")]
    assert adaptive
    assert all(candidate.adaptive_managed for candidate in adaptive)
    assert all(0.0 <= candidate.tail_loss_probability <= 1.0 for candidate in adaptive)
    assert all(candidate.expected_tail_loss >= 0.0 for candidate in adaptive)
    assert all(candidate.cvar_95_loss >= 0.0 for candidate in adaptive)

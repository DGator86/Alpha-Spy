from alpha_spy.strategy_v2_prior import blend_beta_prior


def test_beta_prior_softly_moves_p_and_never_changes_q():
    prediction = {
        "spy_price": 700.0,
        "expected_return": 0.0,
        "sigma_return": 0.001,
        "probability_up": 0.5,
        "payload": {
            "beta_v2": {
                "trust": 0.8,
                "agreement": 0.8,
                "probability_up": 0.75,
                "expected_abs_bps": 12.0,
                "strategy_authority": False,
            },
            "distribution": {
                "p_source": "alpha",
                "p_expected_return": 0.0,
                "p_volatility": 0.001,
                "q_expected_return": 0.0,
                "q_volatility": 0.0012,
                "probability_grid": [0.01, 0.25, 0.5, 0.75, 0.99],
                "p_price_quantiles": [698.6, 699.5, 700.0, 700.5, 701.4],
                "q_price_quantiles": [698.2, 699.4, 700.0, 700.6, 701.8],
            },
        },
    }
    blended = blend_beta_prior(prediction)
    assert blended["expected_return"] > 0.0
    assert blended["payload"]["distribution"]["p_expected_return"] > 0.0
    assert blended["payload"]["distribution"]["q_price_quantiles"] == prediction["payload"]["distribution"]["q_price_quantiles"]
    assert blended["payload"]["beta_v2_prior_blend"]["weight"] <= 0.45
    assert blended["payload"]["beta_v2_prior_blend"]["strategy_authority"] is False

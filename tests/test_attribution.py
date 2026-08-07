from alpha_spy.research.signals.attribution import shapley_attribution


def test_shapley_adds_to_total_increment():
    weights = {"direction": 1.0, "variance": 2.0, "correlation": 3.0}

    def value(active):
        return 10.0 + sum(weights[name] for name in active)

    result = shapley_attribution(list(weights), value)
    assert abs(result.sum() - 6.0) < 1e-12
    assert abs(result["correlation"] - 3.0) < 1e-12

import pandas as pd

from spy_constituent_alpha.models.coverage import complete_by_sector


def test_missing_coverage_is_imputed_and_penalized():
    values = pd.Series({"A": 0.2, "B": None, "C": 0.4})
    weights = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    sectors = {"A": "X", "B": "X", "C": "Y"}
    result = complete_by_sector(values, weights, sectors)
    assert result.completed_values.notna().all()
    assert abs(result.imputed_weight - 0.3) < 1e-12
    assert result.uncertainty_penalty > 0

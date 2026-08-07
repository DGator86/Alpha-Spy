from __future__ import annotations

import numpy as np
import pandas as pd


def _normalize_weights(weights: pd.Series, columns: pd.Index) -> pd.Series:
    w = weights.reindex(columns).fillna(0.0).astype(float)
    total = w.sum()
    if total <= 0:
        raise ValueError("Constituent weights must have positive covered mass")
    return w / total


def breadth_features(
    returns: pd.DataFrame,
    weights: pd.Series,
    *,
    top_n: int = 10,
) -> pd.DataFrame:
    """Compute cap-weighted, equal-weighted, breadth, concentration, and dispersion features."""
    w = _normalize_weights(weights, returns.columns)
    weighted = returns.mul(w, axis=1)
    cap_return = weighted.sum(axis=1, min_count=1)
    equal_return = returns.mean(axis=1)
    adv = returns.gt(0).mul(w, axis=1).sum(axis=1)
    dec = returns.lt(0).mul(w, axis=1).sum(axis=1)
    weighted_abs = weighted.abs().sum(axis=1).replace(0.0, np.nan)
    top = w.nlargest(min(top_n, len(w))).index
    top_contribution = weighted[top].abs().sum(axis=1) / weighted_abs
    dispersion = returns.std(axis=1, ddof=0)
    median_return = returns.median(axis=1)
    participation = returns.notna().mul(w, axis=1).sum(axis=1)

    return pd.DataFrame(
        {
            "cap_return": cap_return,
            "equal_return": equal_return,
            "equal_minus_cap": equal_return - cap_return,
            "weighted_advance": adv,
            "weighted_decline": dec,
            "advance_decline_spread": adv - dec,
            "top_n_contribution": top_contribution,
            "cross_sectional_dispersion": dispersion,
            "median_return": median_return,
            "covered_weight": participation,
        }
    )

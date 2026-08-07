from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CoverageResult:
    completed_values: pd.Series
    observed_weight: float
    imputed_weight: float
    uncertainty_penalty: float
    imputed_tickers: tuple[str, ...]


def complete_by_sector(
    values: pd.Series,
    weights: pd.Series,
    ticker_to_sector: dict[str, str],
    *,
    global_fallback: float | None = None,
    base_uncertainty: float = 0.02,
) -> CoverageResult:
    """Complete missing constituent metrics using weighted sector medians.

    Missing coverage is never silently renormalized. The returned penalty grows with imputed weight and
    cross-sectional disagreement among observed constituents.
    """
    aligned_weights = weights.astype(float).clip(lower=0.0)
    aligned_weights = aligned_weights / aligned_weights.sum()
    observed = values.reindex(aligned_weights.index).astype(float)
    result = observed.copy()
    observed_mask = observed.notna()
    observed_weight = float(aligned_weights[observed_mask].sum())
    imputed: list[str] = []

    weighted_global = global_fallback
    if weighted_global is None:
        if observed_mask.any():
            weighted_global = float(
                np.average(observed[observed_mask], weights=aligned_weights[observed_mask])
            )
        else:
            raise ValueError("No observations and no global fallback are available")

    for ticker in result.index[result.isna()]:
        sector = ticker_to_sector.get(ticker, "UNKNOWN")
        peers = [
            peer
            for peer in result.index
            if ticker_to_sector.get(peer, "UNKNOWN") == sector and pd.notna(observed.get(peer))
        ]
        if peers:
            peer_weights = aligned_weights.reindex(peers)
            result.loc[ticker] = float(np.average(observed.reindex(peers), weights=peer_weights))
        else:
            result.loc[ticker] = float(weighted_global)
        imputed.append(ticker)

    imputed_weight = 1.0 - observed_weight
    dispersion = float(observed[observed_mask].std(ddof=0)) if observed_mask.sum() > 1 else 0.0
    penalty = base_uncertainty * imputed_weight + dispersion * np.sqrt(max(imputed_weight, 0.0))
    return CoverageResult(
        completed_values=result,
        observed_weight=observed_weight,
        imputed_weight=imputed_weight,
        uncertainty_penalty=float(penalty),
        imputed_tickers=tuple(imputed),
    )

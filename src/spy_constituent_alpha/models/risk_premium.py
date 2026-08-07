from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RiskPremiumEstimate:
    expected_premium: float
    uncertainty: float
    observations: int
    regime: str


class CorrelationRiskPremiumModel:
    """Estimate the normal implied-minus-realized correlation premium by regime."""

    def __init__(self, min_observations: int = 60, winsorize_z: float = 4.0):
        self.min_observations = min_observations
        self.winsorize_z = winsorize_z
        self.table_: pd.DataFrame | None = None

    def fit(
        self,
        implied_correlation: pd.Series,
        subsequent_realized_correlation: pd.Series,
        regime: pd.Series | None = None,
    ) -> pd.DataFrame:
        frame = pd.concat(
            [
                implied_correlation.rename("implied"),
                subsequent_realized_correlation.rename("realized"),
            ],
            axis=1,
        ).dropna()
        frame["premium"] = frame["implied"] - frame["realized"]
        if regime is None:
            frame["regime"] = "all"
        else:
            frame["regime"] = regime.reindex(frame.index).fillna("unknown").astype(str)

        med = frame["premium"].median()
        mad = (frame["premium"] - med).abs().median()
        scale = max(1.4826 * mad, 1e-8)
        low, high = med - self.winsorize_z * scale, med + self.winsorize_z * scale
        frame["premium"] = frame["premium"].clip(low, high)

        rows = []
        for name, group in frame.groupby("regime"):
            if len(group) < self.min_observations:
                continue
            rows.append(
                {
                    "regime": name,
                    "expected_premium": group["premium"].mean(),
                    "uncertainty": group["premium"].std(ddof=1) / np.sqrt(len(group)),
                    "observations": len(group),
                }
            )
        if len(frame) >= self.min_observations and not any(r["regime"] == "all" for r in rows):
            rows.append(
                {
                    "regime": "all",
                    "expected_premium": frame["premium"].mean(),
                    "uncertainty": frame["premium"].std(ddof=1) / np.sqrt(len(frame)),
                    "observations": len(frame),
                }
            )
        self.table_ = pd.DataFrame(rows).set_index("regime") if rows else pd.DataFrame()
        return self.table_

    def predict(self, regime: str = "all") -> RiskPremiumEstimate:
        if self.table_ is None or self.table_.empty:
            raise RuntimeError("Risk-premium model has not been fitted")
        key = regime if regime in self.table_.index else "all"
        if key not in self.table_.index:
            key = str(self.table_.index[0])
        row = self.table_.loc[key]
        return RiskPremiumEstimate(
            expected_premium=float(row["expected_premium"]),
            uncertainty=float(row["uncertainty"]),
            observations=int(row["observations"]),
            regime=key,
        )

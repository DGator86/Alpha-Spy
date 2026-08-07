from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ClockConfig(BaseModel):
    timezone: str = "America/New_York"
    max_equity_quote_age_ms: int = 250
    max_option_quote_age_ms: int = 750
    grid_frequency: str = "1s"


class UniverseConfig(BaseModel):
    min_weight: float = 0.0001
    top_n_fast_path: int = 100
    minimum_covered_weight: float = 0.90


class LeadLagConfig(BaseModel):
    lags: list[int] = Field(default_factory=lambda: [0, 1, 2, 5, 10, 30, 60])
    ridge_alpha: float = 10.0
    minimum_training_rows: int = 2000


class CovarianceConfig(BaseModel):
    ewma_halflife: int = 390
    shrinkage: float = 0.35
    downside_threshold: float = 0.0
    minimum_eigenvalue: float = 1e-8


class SimulationConfig(BaseModel):
    paths: int = 30_000
    student_t_df: float = 7.0
    random_seed: int = 86


class RiskPremiumConfig(BaseModel):
    lookback_days: int = 252
    minimum_observations: int = 60
    winsorize_z: float = 4.0


class ScannerConfig(BaseModel):
    min_open_interest: int = 100
    min_option_volume: int = 25
    max_relative_spread: float = 0.20
    min_net_edge_dollars: float = 0.05
    min_edge_to_uncertainty: float = 1.5
    min_probability_positive_edge: float = 0.68
    max_quote_age_ms: int = 750
    allow_single_long_options: bool = True
    allow_vertical_spreads: bool = True


class ExecutionConfig(BaseModel):
    fee_per_contract: float = 0.65
    slippage_fraction_of_spread: float = 0.25
    minimum_slippage: float = 0.01
    contracts_multiplier: int = 100


class Settings(BaseModel):
    clock: ClockConfig = ClockConfig()
    universe: UniverseConfig = UniverseConfig()
    lead_lag: LeadLagConfig = LeadLagConfig()
    covariance: CovarianceConfig = CovarianceConfig()
    simulation: SimulationConfig = SimulationConfig()
    risk_premium: RiskPremiumConfig = RiskPremiumConfig()
    scanner: ScannerConfig = ScannerConfig()
    execution: ExecutionConfig = ExecutionConfig()


def load_settings(path: str | Path | None = None) -> Settings:
    if path is None:
        return Settings()
    with Path(path).open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}
    return Settings.model_validate(raw)

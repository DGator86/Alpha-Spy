from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .config import SuiteConfig
from .db import Journal
from .research.models.covariance import DynamicCovarianceModel, implied_equicorrelation
from .research.models.physical import PhysicalDistributionModel, TerminalDistribution
from .research.models.risk_neutral import SmileSlice, SyntheticRiskNeutralModel
from .timeutil import utc_iso

TRADING_MINUTES_PER_YEAR = 252 * 390


@dataclass(frozen=True)
class DistributionBundle:
    physical: TerminalDistribution
    risk_neutral: TerminalDistribution
    p_source: str
    q_source: str
    covariance_symbols: int
    covariance_observations: int
    covariance_covered_weight: float
    q_surface_symbols: int
    q_surface_covered_weight: float
    realized_correlation: float | None
    implied_correlation: float | None
    correlation_risk_premium: float
    model_uncertainty: float

    def as_dict(self) -> dict[str, Any]:
        # Freeze the *shape* of both distributions, not only their first two moments.
        # The option optimizer reconstructs deterministic stratified scenarios from
        # these quantiles so the P/Q model used for ranking is the same distribution
        # that was frozen into the immutable prediction and later replayed.
        probability_grid = np.linspace(0.005, 0.995, 101)
        p_prices = np.asarray(self.physical.prices, dtype=float)
        q_prices = np.asarray(self.risk_neutral.prices, dtype=float)
        return {
            "p_source": self.p_source,
            "q_source": self.q_source,
            "p_expected_return": self.physical.expected_return,
            "p_volatility": self.physical.volatility,
            "q_expected_return": self.risk_neutral.expected_return,
            "q_volatility": self.risk_neutral.volatility,
            "probability_grid": probability_grid.tolist(),
            "p_price_quantiles": np.quantile(p_prices, probability_grid).tolist(),
            "q_price_quantiles": np.quantile(q_prices, probability_grid).tolist(),
            "covariance_symbols": self.covariance_symbols,
            "covariance_observations": self.covariance_observations,
            "covariance_covered_weight": self.covariance_covered_weight,
            "q_surface_symbols": self.q_surface_symbols,
            "q_surface_covered_weight": self.q_surface_covered_weight,
            "realized_correlation": self.realized_correlation,
            "implied_correlation": self.implied_correlation,
            "correlation_risk_premium": self.correlation_risk_premium,
            "model_uncertainty": self.model_uncertainty,
        }


@dataclass(frozen=True)
class PathMetrics:
    archetype: str
    probability_continuation: float
    probability_reversal: float
    probability_up_1sigma_touch: float
    probability_down_1sigma_touch: float
    probability_up_first: float
    probability_down_first: float
    probability_squeeze_up: float
    probability_liquidation_down: float
    mfe_p50: float
    mfe_p90: float
    mae_p50: float
    mae_p10: float
    terminal_p05: float
    terminal_p50: float
    terminal_p95: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _seed(snapshot_id: str, horizon_minutes: int, namespace: str) -> int:
    raw = f"{namespace}|{snapshot_id}|{horizon_minutes}".encode()
    return int(hashlib.sha256(raw).hexdigest()[:8], 16)


def _fallback_distribution(
    *,
    spot: float,
    expected_return: float,
    sigma_return: float,
    paths: int,
    seed: int,
    measure: str,
    horizon_minutes: int,
) -> TerminalDistribution:
    rng = np.random.default_rng(seed)
    returns = rng.normal(expected_return, max(sigma_return, 1e-6), int(paths))
    prices = spot * np.exp(returns)
    return TerminalDistribution(
        returns=np.expm1(returns),
        prices=prices,
        spot=spot,
        horizon_years=horizon_minutes / TRADING_MINUTES_PER_YEAR,
        measure=measure,
    )


def _constituent_history(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    quotes: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.Series, float]:
    current = [
        row for row in quotes
        if float(row.get("weight") or 0.0) > 0 and row.get("price") is not None
    ]
    current.sort(key=lambda row: float(row.get("weight") or 0.0), reverse=True)
    current = current[: config.prediction.covariance_symbols]
    symbols = [str(row["symbol"]) for row in current]
    weights = pd.Series(
        {str(row["symbol"]): float(row.get("weight") or 0.0) for row in current}, dtype=float
    )
    covered = float(weights.sum())
    rows = journal.constituent_return_matrix(
        symbols,
        str(snapshot["captured_at"]),
        config.prediction.covariance_lookback_rows + 1,
    )
    if not rows:
        return pd.DataFrame(), weights, covered
    frame = pd.DataFrame(rows)
    prices = frame.pivot_table(index="captured_at", columns="symbol", values="price", aggfunc="last")
    prices = prices.sort_index().ffill(limit=2)
    returns = np.log(prices / prices.shift(1)).replace([np.inf, -np.inf], np.nan)
    # Sparse names are removed rather than filled with zero, which would create
    # artificial covariance stability around missing observations.
    min_obs = max(10, config.prediction.minimum_covariance_observations)
    keep = [col for col in returns.columns if int(returns[col].notna().sum()) >= min_obs]
    returns = returns[keep].dropna(how="all")
    weights = weights.reindex(keep).dropna()
    return returns, weights, float(weights.sum())


def _mean_returns(returns: pd.DataFrame) -> pd.Series:
    if returns.empty:
        return pd.Series(dtype=float)
    # Recent data gets more weight but the estimate remains deterministic.
    span = max(10, min(120, len(returns)))
    return returns.ewm(span=span, adjust=False, min_periods=3).mean().iloc[-1].fillna(0.0)


def _latest_iv_observations(
    journal: Journal,
    config: SuiteConfig,
    symbols: list[str],
    captured_at: datetime,
) -> list[dict[str, Any]]:
    not_before = captured_at - timedelta(minutes=config.market.constituent_iv_max_age_minutes)
    return journal.latest_constituent_iv_as_of(
        symbols, utc_iso(not_before), utc_iso(captured_at)
    )


def _smile_from_observation(row: dict[str, Any], tenor: float) -> SmileSlice | None:
    try:
        spot = float(row["underlying_price"])
        atm = float(row["atm_iv"])
    except (KeyError, TypeError, ValueError):
        return None
    if spot <= 0 or atm <= 0:
        return None
    put = row.get("put_25d_iv")
    call = row.get("call_25d_iv")
    try:
        put_iv = float(put) if put is not None else atm * 1.05
        call_iv = float(call) if call is not None else atm * 0.98
    except (TypeError, ValueError):
        put_iv, call_iv = atm * 1.05, atm * 0.98
    vols = np.clip(np.asarray([put_iv, atm, call_iv], dtype=float), 0.01, 5.0)
    return SmileSlice(
        ticker=str(row["symbol"]),
        spot=spot,
        tenor_years=tenor,
        forward=spot,
        log_moneyness=np.asarray([-0.05, 0.0, 0.05], dtype=float),
        implied_volatility=vols,
    )


def build_distribution_bundle(
    journal: Journal,
    config: SuiteConfig,
    *,
    snapshot: dict[str, Any],
    quotes: list[dict[str, Any]],
    horizon_minutes: int,
    fallback_expected_return: float,
    fallback_sigma_return: float,
) -> DistributionBundle:
    spot = float(snapshot.get("spy_price") or 0.0)
    if spot <= 0:
        raise RuntimeError("SPY price unavailable for distribution model")
    horizon_scale = max(float(horizon_minutes), 1.0)
    tenor = horizon_scale / TRADING_MINUTES_PER_YEAR
    returns, weights, covariance_covered_weight = _constituent_history(
        journal, config, snapshot, quotes
    )

    p_source = "fallback_spy_distribution"
    covariance_symbols = 0
    covariance_observations = 0
    covariance = None
    if (
        not returns.empty
        and len(weights) >= 2
        and len(returns) >= config.prediction.minimum_covariance_observations
    ):
        try:
            covariance = DynamicCovarianceModel(
                halflife=max(30, min(config.prediction.covariance_lookback_rows, 390)),
                shrinkage=0.45,
            ).fit(returns)
            mean_returns = _mean_returns(returns)
            physical = PhysicalDistributionModel(
                paths=config.prediction.physical_paths,
                student_t_df=7.0,
                seed=_seed(str(snapshot["snapshot_id"]), horizon_minutes, "P"),
            ).simulate(
                spot=spot,
                weights=weights,
                mean_returns=mean_returns,
                covariance=covariance,
                horizon_scale=horizon_scale,
            )
            # The cross-sectional physical model is allowed to contribute alpha,
            # but is gently anchored to the deterministic constituent forecast.
            blended_returns = 0.70 * physical.returns + 0.30 * fallback_expected_return
            physical = TerminalDistribution(
                returns=blended_returns,
                prices=spot * (1.0 + blended_returns),
                spot=spot,
                horizon_years=tenor,
                measure="P",
            )
            p_source = "constituent_student_t_dynamic_covariance"
            covariance_symbols = len(covariance.covariance.columns)
            covariance_observations = int(covariance.effective_observations)
        except (ValueError, np.linalg.LinAlgError):
            covariance = None
            physical = _fallback_distribution(
                spot=spot,
                expected_return=fallback_expected_return,
                sigma_return=fallback_sigma_return,
                paths=config.prediction.physical_paths,
                seed=_seed(str(snapshot["snapshot_id"]), horizon_minutes, "P-fallback"),
                measure="P",
                horizon_minutes=horizon_minutes,
            )
    else:
        physical = _fallback_distribution(
            spot=spot,
            expected_return=fallback_expected_return,
            sigma_return=fallback_sigma_return,
            paths=config.prediction.physical_paths,
            seed=_seed(str(snapshot["snapshot_id"]), horizon_minutes, "P-fallback"),
            measure="P",
            horizon_minutes=horizon_minutes,
        )

    surface = journal.surface_metrics(str(snapshot["snapshot_id"])) or {}
    spy_iv = surface.get("spy_iv")
    q_source = "spy_iv_lognormal_fallback"
    implied_corr: float | None = None
    realized_corr: float | None = None
    corr_premium = 0.0
    q_surface_weight = 0.0
    q_surface_symbols = 0

    symbols = list(weights.index)
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    iv_rows = _latest_iv_observations(journal, config, symbols, captured_at)
    smiles: dict[str, SmileSlice] = {}
    iv_weights: dict[str, float] = {}
    iv_values: dict[str, float] = {}
    for row in iv_rows:
        smile = _smile_from_observation(row, tenor)
        if smile is None or smile.ticker not in weights.index:
            continue
        smiles[smile.ticker] = smile
        iv_weights[smile.ticker] = float(weights.get(smile.ticker, 0.0))
        iv_values[smile.ticker] = float(row["atm_iv"])
    q_surface_weight = float(sum(iv_weights.values()))
    q_surface_symbols = len(smiles)

    risk_neutral: TerminalDistribution | None = None
    if covariance is not None and len(smiles) >= 2:
        corr = covariance.correlation.reindex(index=list(smiles), columns=list(smiles)).dropna(axis=0, how="all").dropna(axis=1, how="all")
        common = [ticker for ticker in corr.columns if ticker in smiles]
        if len(common) >= 2:
            corr = corr.reindex(index=common, columns=common).fillna(0.0)
            np.fill_diagonal(corr.values, 1.0)
            realized_offdiag = corr.to_numpy()[~np.eye(len(corr), dtype=bool)]
            realized_corr = float(np.nanmedian(realized_offdiag)) if realized_offdiag.size else 0.0
            if spy_iv is not None:
                try:
                    constituent_vols = pd.Series({k: iv_values[k] for k in common}, dtype=float)
                    common_weights = weights.reindex(common).fillna(0.0)
                    implied_corr = float(np.clip(implied_equicorrelation(float(spy_iv) ** 2, constituent_vols, common_weights), -0.95, 0.99))
                    corr_premium = float(np.clip(implied_corr - realized_corr, -0.15, 0.15))
                except (ValueError, ZeroDivisionError, FloatingPointError):
                    implied_corr = None
                    corr_premium = 0.0
            try:
                risk_neutral = SyntheticRiskNeutralModel(
                    paths=config.prediction.risk_neutral_paths,
                    seed=_seed(str(snapshot["snapshot_id"]), horizon_minutes, "Q"),
                ).simulate(
                    index_spot=spot,
                    weights=weights.reindex(common),
                    smiles={k: smiles[k] for k in common},
                    correlation=corr,
                    rate=0.0,
                    correlation_risk_premium=corr_premium,
                    downside_correlation_increment=max(0.0, corr_premium) * 0.35,
                )
                q_source = "constituent_smiles_synthetic_correlation_Q"
            except (ValueError, np.linalg.LinAlgError):
                risk_neutral = None

    if risk_neutral is None:
        annual_iv = float(spy_iv) if spy_iv is not None else max(fallback_sigma_return / math.sqrt(max(tenor, 1e-9)), 0.10)
        q_sigma = annual_iv * math.sqrt(max(tenor, 1e-9))
        risk_neutral = _fallback_distribution(
            spot=spot,
            expected_return=0.0,
            sigma_return=q_sigma,
            paths=config.prediction.risk_neutral_paths,
            seed=_seed(str(snapshot["snapshot_id"]), horizon_minutes, "Q-fallback"),
            measure="Q",
            horizon_minutes=horizon_minutes,
        )

    p_vol = max(physical.volatility, 1e-6)
    q_vol = max(risk_neutral.volatility, 1e-6)
    disagreement = abs(physical.expected_return - risk_neutral.expected_return) + abs(p_vol - q_vol)
    coverage_penalty = max(0.0, config.prediction.minimum_pq_surface_weight - q_surface_weight)
    covariance_penalty = max(0.0, 0.50 - covariance_covered_weight)
    model_uncertainty = float(
        min(
            config.prediction.max_model_uncertainty,
            0.35 * disagreement + 0.01 * coverage_penalty + 0.005 * covariance_penalty,
        )
    )
    return DistributionBundle(
        physical=physical,
        risk_neutral=risk_neutral,
        p_source=p_source,
        q_source=q_source,
        covariance_symbols=covariance_symbols,
        covariance_observations=covariance_observations,
        covariance_covered_weight=covariance_covered_weight,
        q_surface_symbols=q_surface_symbols,
        q_surface_covered_weight=q_surface_weight,
        realized_correlation=realized_corr,
        implied_correlation=implied_corr,
        correlation_risk_premium=corr_premium,
        model_uncertainty=model_uncertainty,
    )


def simulate_path_metrics(
    *,
    snapshot_id: str,
    horizon_minutes: int,
    expected_return: float,
    sigma_return: float,
    gamma_state: str,
    breadth: float,
    order_flow_imbalance: float,
    paths: int = 2500,
) -> PathMetrics:
    steps = max(2, min(int(horizon_minutes), 390))
    rng = np.random.default_rng(_seed(snapshot_id, horizon_minutes, "path"))
    mean_step = expected_return / steps
    sigma_step = max(sigma_return, 1e-6) / math.sqrt(steps)
    increments = rng.normal(mean_step, sigma_step, size=(paths, steps))
    cumulative = np.cumsum(increments, axis=1)
    terminal = cumulative[:, -1]
    mfe = np.max(cumulative, axis=1)
    mae = np.min(cumulative, axis=1)
    threshold = max(sigma_return, 1e-6)
    up_touch = cumulative >= threshold
    down_touch = cumulative <= -threshold
    hit_up = up_touch.any(axis=1)
    hit_down = down_touch.any(axis=1)
    up_first_idx = np.where(hit_up, np.argmax(up_touch, axis=1), steps + 1)
    down_first_idx = np.where(hit_down, np.argmax(down_touch, axis=1), steps + 1)
    both = hit_up & hit_down
    up_first = hit_up & (~hit_down | (up_first_idx < down_first_idx))
    down_first = hit_down & (~hit_up | (down_first_idx < up_first_idx))

    quarter = cumulative[:, max(0, steps // 4 - 1)]
    continuation = np.sign(quarter) == np.sign(terminal)
    reversal = (np.sign(quarter) != 0) & (np.sign(terminal) == -np.sign(quarter))
    squeeze_base = np.mean((mfe >= 1.5 * threshold) & (terminal > 0))
    liquidation_base = np.mean((mae <= -1.5 * threshold) & (terminal < 0))
    gamma_multiplier = 1.20 if gamma_state == "negative_gamma" else 0.90 if gamma_state == "positive_gamma" else 1.0
    breadth_up = max(0.0, (breadth - 0.5) * 2.0)
    breadth_down = max(0.0, (0.5 - breadth) * 2.0)
    flow_up = max(0.0, order_flow_imbalance)
    flow_down = max(0.0, -order_flow_imbalance)
    squeeze = min(1.0, float(squeeze_base) * gamma_multiplier * (1.0 + 0.35 * breadth_up + 0.25 * flow_up))
    liquidation = min(1.0, float(liquidation_base) * gamma_multiplier * (1.0 + 0.35 * breadth_down + 0.25 * flow_down))

    abs_mean = abs(expected_return)
    if abs_mean >= 0.40 * threshold:
        archetype = "trend_up" if expected_return > 0 else "trend_down"
    elif float(np.mean(both)) >= 0.25:
        archetype = "two_sided_mean_reversion"
    elif max(float(np.mean(hit_up)), float(np.mean(hit_down))) < 0.35:
        archetype = "compression_range"
    else:
        archetype = "mixed_path"
    return PathMetrics(
        archetype=archetype,
        probability_continuation=float(np.mean(continuation)),
        probability_reversal=float(np.mean(reversal)),
        probability_up_1sigma_touch=float(np.mean(hit_up)),
        probability_down_1sigma_touch=float(np.mean(hit_down)),
        probability_up_first=float(np.mean(up_first)),
        probability_down_first=float(np.mean(down_first)),
        probability_squeeze_up=squeeze,
        probability_liquidation_down=liquidation,
        mfe_p50=float(np.quantile(mfe, 0.50)),
        mfe_p90=float(np.quantile(mfe, 0.90)),
        mae_p50=float(np.quantile(mae, 0.50)),
        mae_p10=float(np.quantile(mae, 0.10)),
        terminal_p05=float(np.quantile(terminal, 0.05)),
        terminal_p50=float(np.quantile(terminal, 0.50)),
        terminal_p95=float(np.quantile(terminal, 0.95)),
    )

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

from alpha_spy.research.domain import EdgeEstimate
from alpha_spy.research.models.physical import TerminalDistribution


@dataclass(frozen=True)
class ScanSettings:
    fee_per_contract: float = 0.65
    slippage_fraction_of_spread: float = 0.25
    minimum_slippage: float = 0.01
    multiplier: int = 100
    min_open_interest: int = 100
    min_volume: int = 25
    max_relative_spread: float = 0.20
    max_quote_age_ms: int = 750
    min_net_edge: float = 0.05
    min_edge_to_uncertainty: float = 1.5
    min_probability_positive_edge: float = 0.68


def discounted_expected_payoff(
    distribution: TerminalDistribution,
    strike: float,
    right: str,
    rate: float,
) -> float:
    return float(np.exp(-rate * distribution.horizon_years) * distribution.expected_payoff(strike, right))


def quote_is_eligible(row: pd.Series, settings: ScanSettings, now: datetime) -> bool:
    midpoint = max((float(row.bid) + float(row.ask)) / 2.0, 0.01)
    relative_spread = (float(row.ask) - float(row.bid)) / midpoint
    ts = pd.Timestamp(row.timestamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    age_ms = (now_ts.tz_convert("UTC") - ts.tz_convert("UTC")).total_seconds() * 1000.0
    return bool(
        row.bid >= 0
        and row.ask >= row.bid
        and relative_spread <= settings.max_relative_spread
        and row.open_interest >= settings.min_open_interest
        and row.volume >= settings.min_volume
        and age_ms <= settings.max_quote_age_ms
    )


def scan_long_options(
    chain: pd.DataFrame,
    *,
    risk_neutral: TerminalDistribution,
    physical: TerminalDistribution,
    rate: float,
    model_uncertainty: float,
    settings: ScanSettings = ScanSettings(),
    now: datetime | None = None,
) -> list[EdgeEstimate]:
    now = now or datetime.now(UTC)
    results: list[EdgeEstimate] = []
    for row in chain.itertuples(index=False):
        series = pd.Series(row._asdict())
        if not quote_is_eligible(series, settings, now):
            continue
        right = str(row.right)
        model_value = discounted_expected_payoff(risk_neutral, float(row.strike), right, rate)
        physical_payoff = discounted_expected_payoff(physical, float(row.strike), right, rate)
        spread = float(row.ask) - float(row.bid)
        slippage = max(settings.minimum_slippage, settings.slippage_fraction_of_spread * spread)
        market_entry = float(row.ask) + slippage
        execution_cost = slippage + settings.fee_per_contract / settings.multiplier
        gross_edge = model_value - float(row.ask)
        uncertainty = max(float(model_uncertainty), 1e-6)
        net_edge = model_value - market_entry - settings.fee_per_contract / settings.multiplier
        probability = float(norm.cdf(net_edge / uncertainty))
        edge_ratio = net_edge / uncertainty
        if (
            net_edge >= settings.min_net_edge
            and edge_ratio >= settings.min_edge_to_uncertainty
            and probability >= settings.min_probability_positive_edge
        ):
            results.append(
                EdgeEstimate(
                    symbol=str(row.symbol),
                    structure=f"LONG_{right.upper()[0]}",
                    model_value=model_value,
                    market_entry=market_entry,
                    gross_edge=gross_edge,
                    execution_cost=execution_cost,
                    uncertainty=uncertainty,
                    net_edge=net_edge,
                    probability_positive_edge=probability,
                    max_profit=float("inf"),
                    max_loss=market_entry,
                    expected_payoff_physical=physical_payoff,
                    metadata={
                        "strike": float(row.strike),
                        "bid": float(row.bid),
                        "ask": float(row.ask),
                        "physical_expected_net": physical_payoff - market_entry,
                    },
                )
            )
    return sorted(results, key=lambda x: (x.net_edge / x.uncertainty, x.net_edge), reverse=True)


def scan_vertical_spreads(
    chain: pd.DataFrame,
    *,
    risk_neutral: TerminalDistribution,
    physical: TerminalDistribution,
    rate: float,
    model_uncertainty: float,
    settings: ScanSettings = ScanSettings(),
    now: datetime | None = None,
    max_width: float = 20.0,
) -> list[EdgeEstimate]:
    """Scan debit and credit verticals. Every returned structure has finite maximum loss."""
    now = now or datetime.now(UTC)
    eligible_rows = []
    for row in chain.itertuples(index=False):
        series = pd.Series(row._asdict())
        if quote_is_eligible(series, settings, now):
            eligible_rows.append(row)
    results: list[EdgeEstimate] = []
    for right in ("C", "P"):
        rows = sorted([r for r in eligible_rows if str(r.right).upper().startswith(right)], key=lambda r: r.strike)
        for i, low in enumerate(rows):
            for high in rows[i + 1 :]:
                width = float(high.strike - low.strike)
                if width <= 0 or width > max_width:
                    continue
                if right == "C":
                    long_leg, short_leg = low, high
                else:
                    long_leg, short_leg = high, low
                model_long = discounted_expected_payoff(risk_neutral, float(long_leg.strike), right, rate)
                model_short = discounted_expected_payoff(risk_neutral, float(short_leg.strike), right, rate)
                physical_long = discounted_expected_payoff(physical, float(long_leg.strike), right, rate)
                physical_short = discounted_expected_payoff(physical, float(short_leg.strike), right, rate)
                model_value = model_long - model_short
                entry_debit = float(long_leg.ask) - float(short_leg.bid)
                combined_spread = (float(long_leg.ask) - float(long_leg.bid)) + (
                    float(short_leg.ask) - float(short_leg.bid)
                )
                slippage = max(settings.minimum_slippage, settings.slippage_fraction_of_spread * combined_spread)
                fees = 2.0 * settings.fee_per_contract / settings.multiplier
                market_entry = entry_debit + slippage
                net_edge = model_value - market_entry - fees
                uncertainty = max(model_uncertainty * np.sqrt(2.0), 1e-6)
                probability = float(norm.cdf(net_edge / uncertainty))
                max_loss = max(entry_debit + slippage + fees, 0.0)
                max_profit = max(width - max_loss, 0.0)
                if (
                    net_edge >= settings.min_net_edge
                    and net_edge / uncertainty >= settings.min_edge_to_uncertainty
                    and probability >= settings.min_probability_positive_edge
                ):
                    results.append(
                        EdgeEstimate(
                            symbol=f"{long_leg.symbol}/{short_leg.symbol}",
                            structure=f"{right}_DEBIT_VERTICAL",
                            model_value=model_value,
                            market_entry=market_entry,
                            gross_edge=model_value - entry_debit,
                            execution_cost=slippage + fees,
                            uncertainty=uncertainty,
                            net_edge=net_edge,
                            probability_positive_edge=probability,
                            max_profit=max_profit,
                            max_loss=max_loss,
                            expected_payoff_physical=physical_long - physical_short,
                            metadata={
                                "long_strike": float(long_leg.strike),
                                "short_strike": float(short_leg.strike),
                                "width": width,
                                "physical_expected_net": physical_long - physical_short - market_entry - fees,
                            },
                        )
                    )
    return sorted(results, key=lambda x: (x.net_edge / x.uncertainty, x.net_edge), reverse=True)

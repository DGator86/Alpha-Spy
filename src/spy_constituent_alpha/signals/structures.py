from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

from spy_constituent_alpha.domain import EdgeEstimate
from spy_constituent_alpha.models.physical import TerminalDistribution
from spy_constituent_alpha.signals.mispricing import (
    ScanSettings,
    discounted_expected_payoff,
    quote_is_eligible,
)


def _probability(net_edge: float, uncertainty: float) -> float:
    return float(norm.cdf(net_edge / max(uncertainty, 1e-9)))


def _passes(net_edge: float, uncertainty: float, settings: ScanSettings) -> bool:
    return bool(
        net_edge >= settings.min_net_edge
        and net_edge / max(uncertainty, 1e-9) >= settings.min_edge_to_uncertainty
        and _probability(net_edge, uncertainty) >= settings.min_probability_positive_edge
    )


def _eligible(chain: pd.DataFrame, settings: ScanSettings, now: datetime) -> list:
    rows = []
    for row in chain.itertuples(index=False):
        if quote_is_eligible(pd.Series(row._asdict()), settings, now):
            rows.append(row)
    return rows


def scan_credit_verticals(
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
    now = now or datetime.now(UTC)
    rows = _eligible(chain, settings, now)
    results: list[EdgeEstimate] = []
    for right in ("C", "P"):
        options = sorted([r for r in rows if str(r.right).upper().startswith(right)], key=lambda r: r.strike)
        for i, low in enumerate(options):
            for high in options[i + 1 :]:
                width = float(high.strike - low.strike)
                if width <= 0 or width > max_width:
                    continue
                if right == "C":
                    short_leg, hedge_leg = low, high
                else:
                    short_leg, hedge_leg = high, low
                fair_liability = discounted_expected_payoff(
                    risk_neutral, float(short_leg.strike), right, rate
                ) - discounted_expected_payoff(risk_neutral, float(hedge_leg.strike), right, rate)
                physical_liability = discounted_expected_payoff(
                    physical, float(short_leg.strike), right, rate
                ) - discounted_expected_payoff(physical, float(hedge_leg.strike), right, rate)
                raw_credit = float(short_leg.bid) - float(hedge_leg.ask)
                combined_spread = (short_leg.ask - short_leg.bid) + (hedge_leg.ask - hedge_leg.bid)
                slippage = max(settings.minimum_slippage, settings.slippage_fraction_of_spread * combined_spread)
                fees = 2.0 * settings.fee_per_contract / settings.multiplier
                executable_credit = raw_credit - slippage
                net_edge = executable_credit - fair_liability - fees
                uncertainty = max(model_uncertainty * np.sqrt(2.0), 1e-6)
                if executable_credit <= 0 or not _passes(net_edge, uncertainty, settings):
                    continue
                max_profit = max(executable_credit - fees, 0.0)
                max_loss = max(width - executable_credit + fees, 0.0)
                results.append(
                    EdgeEstimate(
                        symbol=f"{short_leg.symbol}/{hedge_leg.symbol}",
                        structure=f"{right}_CREDIT_VERTICAL",
                        model_value=fair_liability,
                        market_entry=executable_credit,
                        gross_edge=raw_credit - fair_liability,
                        execution_cost=slippage + fees,
                        uncertainty=uncertainty,
                        net_edge=net_edge,
                        probability_positive_edge=_probability(net_edge, uncertainty),
                        max_profit=max_profit,
                        max_loss=max_loss,
                        expected_payoff_physical=executable_credit - physical_liability,
                        metadata={
                            "short_strike": float(short_leg.strike),
                            "hedge_strike": float(hedge_leg.strike),
                            "width": width,
                            "physical_expected_net": executable_credit - physical_liability - fees,
                        },
                    )
                )
    return sorted(results, key=lambda x: (x.net_edge / x.uncertainty, x.net_edge), reverse=True)


def scan_butterflies(
    chain: pd.DataFrame,
    *,
    risk_neutral: TerminalDistribution,
    physical: TerminalDistribution,
    rate: float,
    model_uncertainty: float,
    settings: ScanSettings = ScanSettings(),
    now: datetime | None = None,
    max_wing_width: float = 10.0,
) -> list[EdgeEstimate]:
    now = now or datetime.now(UTC)
    rows = _eligible(chain, settings, now)
    results: list[EdgeEstimate] = []
    for right in ("C", "P"):
        options = sorted([r for r in rows if str(r.right).upper().startswith(right)], key=lambda r: r.strike)
        by_strike = {round(float(r.strike), 8): r for r in options}
        strikes = sorted(by_strike)
        for center in strikes:
            for lower in [s for s in strikes if s < center]:
                wing = center - lower
                upper = round(center + wing, 8)
                if wing <= 0 or wing > max_wing_width or upper not in by_strike:
                    continue
                low, mid, high = by_strike[lower], by_strike[center], by_strike[upper]
                fair = (
                    discounted_expected_payoff(risk_neutral, lower, right, rate)
                    - 2.0 * discounted_expected_payoff(risk_neutral, center, right, rate)
                    + discounted_expected_payoff(risk_neutral, upper, right, rate)
                )
                physical_value = (
                    discounted_expected_payoff(physical, lower, right, rate)
                    - 2.0 * discounted_expected_payoff(physical, center, right, rate)
                    + discounted_expected_payoff(physical, upper, right, rate)
                )
                debit = float(low.ask) - 2.0 * float(mid.bid) + float(high.ask)
                total_spread = (low.ask - low.bid) + 2.0 * (mid.ask - mid.bid) + (high.ask - high.bid)
                slippage = max(settings.minimum_slippage, settings.slippage_fraction_of_spread * total_spread)
                fees = 4.0 * settings.fee_per_contract / settings.multiplier
                executable_debit = debit + slippage
                net_edge = fair - executable_debit - fees
                uncertainty = max(model_uncertainty * 2.0, 1e-6)
                if executable_debit <= 0 or not _passes(net_edge, uncertainty, settings):
                    continue
                results.append(
                    EdgeEstimate(
                        symbol=f"{low.symbol}/-2x{mid.symbol}/{high.symbol}",
                        structure=f"{right}_BUTTERFLY",
                        model_value=fair,
                        market_entry=executable_debit,
                        gross_edge=fair - debit,
                        execution_cost=slippage + fees,
                        uncertainty=uncertainty,
                        net_edge=net_edge,
                        probability_positive_edge=_probability(net_edge, uncertainty),
                        max_profit=max(wing - executable_debit - fees, 0.0),
                        max_loss=executable_debit + fees,
                        expected_payoff_physical=physical_value,
                        metadata={
                            "lower_strike": lower,
                            "center_strike": center,
                            "upper_strike": upper,
                            "physical_expected_net": physical_value - executable_debit - fees,
                        },
                    )
                )
    return sorted(results, key=lambda x: (x.net_edge / x.uncertainty, x.net_edge), reverse=True)


def scan_iron_condors(
    chain: pd.DataFrame,
    *,
    risk_neutral: TerminalDistribution,
    physical: TerminalDistribution,
    rate: float,
    model_uncertainty: float,
    settings: ScanSettings = ScanSettings(),
    now: datetime | None = None,
    max_wing_width: float = 8.0,
    min_body_width: float = 2.0,
) -> list[EdgeEstimate]:
    now = now or datetime.now(UTC)
    rows = _eligible(chain, settings, now)
    puts = sorted([r for r in rows if str(r.right).upper().startswith("P")], key=lambda r: r.strike)
    calls = sorted([r for r in rows if str(r.right).upper().startswith("C")], key=lambda r: r.strike)
    results: list[EdgeEstimate] = []
    for long_put in puts:
        for short_put in [p for p in puts if p.strike > long_put.strike]:
            put_width = float(short_put.strike - long_put.strike)
            if put_width <= 0 or put_width > max_wing_width:
                continue
            for short_call in [c for c in calls if c.strike >= short_put.strike + min_body_width]:
                for long_call in [c for c in calls if c.strike > short_call.strike]:
                    call_width = float(long_call.strike - short_call.strike)
                    if call_width <= 0 or call_width > max_wing_width:
                        continue
                    fair_liability = (
                        discounted_expected_payoff(risk_neutral, short_put.strike, "P", rate)
                        - discounted_expected_payoff(risk_neutral, long_put.strike, "P", rate)
                        + discounted_expected_payoff(risk_neutral, short_call.strike, "C", rate)
                        - discounted_expected_payoff(risk_neutral, long_call.strike, "C", rate)
                    )
                    physical_liability = (
                        discounted_expected_payoff(physical, short_put.strike, "P", rate)
                        - discounted_expected_payoff(physical, long_put.strike, "P", rate)
                        + discounted_expected_payoff(physical, short_call.strike, "C", rate)
                        - discounted_expected_payoff(physical, long_call.strike, "C", rate)
                    )
                    raw_credit = (
                        short_put.bid - long_put.ask + short_call.bid - long_call.ask
                    )
                    total_spread = sum(
                        leg.ask - leg.bid for leg in [long_put, short_put, short_call, long_call]
                    )
                    slippage = max(settings.minimum_slippage, settings.slippage_fraction_of_spread * total_spread)
                    fees = 4.0 * settings.fee_per_contract / settings.multiplier
                    executable_credit = raw_credit - slippage
                    net_edge = executable_credit - fair_liability - fees
                    uncertainty = max(model_uncertainty * 2.0, 1e-6)
                    if executable_credit <= 0 or not _passes(net_edge, uncertainty, settings):
                        continue
                    max_width = max(put_width, call_width)
                    results.append(
                        EdgeEstimate(
                            symbol=(
                                f"{long_put.symbol}/{short_put.symbol}/"
                                f"{short_call.symbol}/{long_call.symbol}"
                            ),
                            structure="IRON_CONDOR",
                            model_value=fair_liability,
                            market_entry=executable_credit,
                            gross_edge=raw_credit - fair_liability,
                            execution_cost=slippage + fees,
                            uncertainty=uncertainty,
                            net_edge=net_edge,
                            probability_positive_edge=_probability(net_edge, uncertainty),
                            max_profit=max(executable_credit - fees, 0.0),
                            max_loss=max(max_width - executable_credit + fees, 0.0),
                            expected_payoff_physical=executable_credit - physical_liability,
                            metadata={
                                "long_put": float(long_put.strike),
                                "short_put": float(short_put.strike),
                                "short_call": float(short_call.strike),
                                "long_call": float(long_call.strike),
                                "physical_expected_net": executable_credit - physical_liability - fees,
                            },
                        )
                    )
    return sorted(results, key=lambda x: (x.net_edge / x.uncertainty, x.net_edge), reverse=True)

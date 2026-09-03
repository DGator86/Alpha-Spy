from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from .assertions import EconomicRole, QUANT_STREAM_ACCESS, QuantRole

Desk = Literal["QUANT", "ECONOMICS"]


@dataclass(frozen=True)
class AnalystSpec:
    role: QuantRole | EconomicRole
    desk: Desk
    mission: str
    inputs: tuple[str, ...]
    required_questions: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    manager_destination: Literal["QUANT_MANAGER", "ECONOMIST"]

    def as_dict(self):
        return asdict(self)


QUANT_SPECS: dict[QuantRole, AnalystSpec] = {
    "DIRECTION_MOMENTUM": AnalystSpec(
        role="DIRECTION_MOMENTUM",
        desk="QUANT",
        mission="Assess directional continuation/reversal pressure by horizon without selecting a trade.",
        inputs=QUANT_STREAM_ACCESS["DIRECTION_MOMENTUM"],
        required_questions=(
            "Which direction has the stronger measured path by horizon?",
            "Are Alpha, Beta, and Gamma confirming or diverging?",
            "Is momentum strengthening, decelerating, or reversing?",
        ),
        prohibited_actions=("select_strategy", "select_option", "size_position", "send_order"),
        manager_destination="QUANT_MANAGER",
    ),
    "MARKET_INTERNALS": AnalystSpec(
        role="MARKET_INTERNALS",
        desk="QUANT",
        mission="Determine whether SPY's observed move is supported by constituents, sectors, participation, and tape.",
        inputs=QUANT_STREAM_ACCESS["MARKET_INTERNALS"],
        required_questions=(
            "Is index direction broad or concentrated?",
            "Are leadership and laggards confirming the index?",
            "Is tape/quote behavior strengthening or weakening participation?",
        ),
        prohibited_actions=("select_strategy", "select_option", "size_position", "send_order"),
        manager_destination="QUANT_MANAGER",
    ),
    "VOLATILITY_DERIVATIVES": AnalystSpec(
        role="VOLATILITY_DERIVATIVES",
        desk="QUANT",
        mission="Interpret volatility, option positioning, term structure, skew, pinning, and liquidity as path constraints.",
        inputs=QUANT_STREAM_ACCESS["VOLATILITY_DERIVATIVES"],
        required_questions=(
            "Is implied volatility expanding or compressing across expirations?",
            "What positioning proxies can accelerate, damp, or pin SPY?",
            "How reliable are the derivatives measurements and liquidity?",
        ),
        prohibited_actions=("select_strategy", "select_option", "size_position", "send_order"),
        manager_destination="QUANT_MANAGER",
    ),
    "STATISTICAL_REGIME": AnalystSpec(
        role="STATISTICAL_REGIME",
        desk="QUANT",
        mission="Challenge visual narratives with Alpha's distributions, regime, lifecycle, and calibrated uncertainty.",
        inputs=QUANT_STREAM_ACCESS["STATISTICAL_REGIME"],
        required_questions=(
            "What does the conditional distribution imply by horizon?",
            "How stable is the current regime and how old is it?",
            "Are path/tail probabilities consistent with the prevailing narrative?",
        ),
        prohibited_actions=("select_strategy", "select_option", "size_position", "send_order"),
        manager_destination="QUANT_MANAGER",
    ),
    "QUANT_SKEPTIC": AnalystSpec(
        role="QUANT_SKEPTIC",
        desk="QUANT",
        mission="Actively search for reasons the prevailing quantitative thesis is wrong or overconfident.",
        inputs=QUANT_STREAM_ACCESS["QUANT_SKEPTIC"],
        required_questions=(
            "What evidence contradicts the apparent consensus?",
            "Which signals are stale, weak, circular, or low quality?",
            "Where are horizons or models materially diverging?",
            "What observable condition would falsify the consensus?",
        ),
        prohibited_actions=("create_consensus", "select_strategy", "select_option", "size_position", "send_order"),
        manager_destination="QUANT_MANAGER",
    ),
}


ECONOMIC_SPECS: dict[EconomicRole, AnalystSpec] = {
    "MACRO": AnalystSpec(
        role="MACRO",
        desk="ECONOMICS",
        mission="Assess growth, inflation, labor, credit, liquidity, dollar, commodity, and financial-condition context.",
        inputs=("macro_releases", "macro_nowcasts", "credit", "dollar", "commodities", "financial_conditions"),
        required_questions=("What macro impulse is active?", "What upcoming macro event can change it?"),
        prohibited_actions=("overwrite_quant_data", "select_strategy", "send_order"),
        manager_destination="ECONOMIST",
    ),
    "RATES_FED": AnalystSpec(
        role="RATES_FED",
        desk="ECONOMICS",
        mission="Assess Fed policy, Treasury curve, SOFR, real yields, inflation expectations, and rate-probability impulses.",
        inputs=("fed", "treasury_curve", "sofr", "real_yields", "breakevens", "rate_probabilities"),
        required_questions=("What is the current rates impulse?", "What policy event or speaker is next?"),
        prohibited_actions=("overwrite_quant_data", "select_strategy", "send_order"),
        manager_destination="ECONOMIST",
    ),
    "NEWS_CATALYST": AnalystSpec(
        role="NEWS_CATALYST",
        desk="ECONOMICS",
        mission="Identify verified breaking catalysts that prices/models may not yet fully encode.",
        inputs=("breaking_news", "earnings", "constituent_events", "geopolitics", "regulation", "index_events"),
        required_questions=("What just happened?", "Is it verified?", "Which SPY transmission channels are exposed?"),
        prohibited_actions=("treat_unverified_social_post_as_fact", "overwrite_quant_data", "send_order"),
        manager_destination="ECONOMIST",
    ),
    "INFORMATION_FLOW_SENTIMENT": AnalystSpec(
        role="INFORMATION_FLOW_SENTIMENT",
        desk="ECONOMICS",
        mission="Measure narrative velocity, crowd attention, and social-information flow as low-authority context.",
        inputs=("social_velocity", "headline_velocity", "narrative_clusters", "crowd_positioning_proxies"),
        required_questions=("Which narratives are accelerating?", "Is attention crowded or changing abruptly?"),
        prohibited_actions=("promote_social_claim_to_news", "overwrite_quant_data", "send_order"),
        manager_destination="ECONOMIST",
    ),
}


def analyst_spec(role: QuantRole | EconomicRole) -> AnalystSpec:
    if role in QUANT_SPECS:
        return QUANT_SPECS[role]  # type: ignore[index]
    if role in ECONOMIC_SPECS:
        return ECONOMIC_SPECS[role]  # type: ignore[index]
    raise KeyError(role)

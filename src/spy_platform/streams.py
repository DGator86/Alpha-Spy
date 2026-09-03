from __future__ import annotations

from typing import Any

from .contracts import DeltaState


def build_delta_streams(delta: DeltaState) -> dict[str, dict[str, Any]]:
    """Expose bounded analytical views over one immutable Delta state.

    Streams are measurements, not recommendations.  They intentionally contain no
    trade action, position sizing, instrument selection, or broker instruction.
    """
    base = {
        "timestamp": delta.timestamp,
        "schema_version": delta.schema_version,
        "authority": delta.authority,
    }
    return {
        "direction": {
            **base,
            "alpha_probability_up": delta.alpha.probability_up,
            "alpha_expected_return_bps": delta.alpha.expected_return_bps,
            "beta_probability_up": delta.beta.probability_up,
            "beta_expected_return_bps": delta.beta.expected_return_bps,
            "gamma_directional_score": delta.gamma.directional_score,
            "convergence": delta.convergence.get("horizons", {}),
        },
        "regime": {
            **base,
            "alpha_regime": delta.alpha.regime,
            "alpha_lifecycle": delta.alpha.lifecycle,
            "alpha_uncertainty": delta.alpha.uncertainty,
        },
        "path": {
            **base,
            "alpha_expected_mfe_bps": delta.alpha.expected_mfe_bps,
            "alpha_expected_mae_bps": delta.alpha.expected_mae_bps,
            "alpha_distributions": delta.alpha.distributions,
        },
        "breadth": {
            **base,
            "breadth": delta.beta.breadth,
            "sectors": delta.beta.sectors,
            "participation": delta.beta.participation,
        },
        "flow": {
            **base,
            "flow": delta.beta.flow,
            "microstructure": delta.beta.microstructure,
        },
        "volatility": {
            **base,
            "alpha_metrics": delta.alpha.metrics,
            "iv_surface": delta.gamma.iv_surface,
            "term_structure": delta.gamma.term_structure,
            "skew": delta.gamma.skew,
        },
        "options_positioning": {
            **base,
            "activity": delta.gamma.activity,
            "positioning": delta.gamma.positioning,
            "risk_states": delta.gamma.risk_states,
        },
        "liquidity": {
            **base,
            "gamma_liquidity": delta.gamma.liquidity,
            "beta_microstructure": delta.beta.microstructure,
        },
        "divergence": {
            **base,
            "convergence": delta.convergence,
            "conflicts": delta.conflicts,
            "model_changes": delta.model_changes,
        },
        "anomalies": {
            **base,
            "anomalies": delta.anomalies,
            "conflicts": delta.conflicts,
        },
        "data_quality": {
            **base,
            **delta.data_quality,
        },
    }

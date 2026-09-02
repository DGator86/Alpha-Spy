from __future__ import annotations

# Any change that can materially alter regime classification, lifecycle authority,
# opportunity selection, option geometry, execution economics, or trade management
# MUST advance this version. Step-16 governance counts forward evidence only from
# the current version, preventing old forward trades from laundering evidence into
# a materially changed policy.
CURRENT_POLICY_VERSION = "alpha-v2-closed-loop-2026-09-02-r1"

# Human-readable architecture contract for audit/export surfaces.
POLICY_CONTRACT = {
    "regime": "horizon_specific_tie_safe_alpha_hierarchy",
    "duration": "blocked_discrete_time_risk_set_survival",
    "successor": "calibrated_advisory_until_skill_gate",
    "beta_role": "independent_exact_target_witness_no_strategy_authority",
    "directional_control": "hgb_actual_chain_vertical_3x_drag_no_arbitrage",
    "execution": "paper_sandbox_only_broker_preview_fail_closed",
    "management": "thesis_specific_no_unvalidated_successor_authority",
    "governance": "forward_actual_chain_independent_sessions_confidence_bounds",
}

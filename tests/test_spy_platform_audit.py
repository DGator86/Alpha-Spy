from __future__ import annotations

from spy_platform.assertions import AnalystAssertion
from spy_platform.audit import AuditLedger
from spy_platform.contracts import AlphaState, BetaState, ModelMeta
from spy_platform.delta import compile_delta_state
from spy_platform.gamma import build_gamma_state
from spy_platform.routing import route_assertion


def _state():
    alpha = AlphaState(
        meta=ModelMeta.create(
            model="ALPHA",
            timestamp="2026-09-03T14:42:00Z",
            model_version="alpha-test",
            data_quality=1.0,
        ),
        probability_up={15: 0.60},
    )
    beta = BetaState(
        meta=ModelMeta.create(
            model="BETA",
            timestamp="2026-09-03T14:42:00Z",
            model_version="beta-test",
            data_quality=1.0,
        ),
        probability_up={15: 0.62},
    )
    gamma = build_gamma_state(
        timestamp="2026-09-03T14:42:00Z",
        spot=650.0,
        chains=[],
    )
    return compile_delta_state(alpha, beta, gamma)


def test_audit_ledger_is_append_only_and_deduplicates_identical_delta_state(tmp_path):
    ledger = AuditLedger(tmp_path / "audit.sqlite")
    state = _state()
    first = ledger.record_delta(state)
    second = ledger.record_delta(state)
    assert first == second
    assert ledger.count("delta_states") == 1


def test_assertion_is_frozen_with_manager_destination(tmp_path):
    ledger = AuditLedger(tmp_path / "audit.sqlite")
    assertion = AnalystAssertion(
        assertion_id="Q-1",
        timestamp="2026-09-03T14:42:00Z",
        role="QUANT_SKEPTIC",
        thesis="Current consensus may be overstating certainty.",
        confidence=0.63,
        horizon_minutes=15,
        evidence=("delta/divergence",),
    )
    routed = route_assertion(assertion)
    ledger.record_assertion(assertion, destination=routed.destination)
    ledger.record_assertion(assertion, destination=routed.destination)
    assert ledger.count("analyst_assertions") == 1

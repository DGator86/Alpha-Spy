from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from . import v2_engine as engine_module
from .timeutil import ET
from .v2_hgb_vertical_repaired import build_hgb_vertical_candidate
from .v2_lifecycle_survival import AlphaRiskSetLifecycleEngine
from .v2_policy import CURRENT_POLICY_VERSION, POLICY_CONTRACT
from .v2_regime_repaired import classify_regime_hierarchy
from .v2_state_pq import generate_state_pq_candidates as _generate_state_pq_candidates
from .v2_trader_agent import PLAYBOOK_DIRECTIONAL
from .v2_trader_agent_repaired import build_agent_plan

_HGB_AUTHORITY = "beta_v2_hgb_blocked_walk_forward"
_TRADER_AGENT_AUTHORITY = "alpha_v2_closed_loop_trader_agent"
_DIRECTIONAL_DATE_CONTROL = "v2_directional_setup_session_date"
_FORWARD_ACTUAL_CHAIN = "FORWARD_ACTUAL_CHAIN"
_REPLAY_OR_UNVERIFIED = "REPLAY_OR_UNVERIFIED"
_CHAIN_FINGERPRINT_FIELDS = (
    "symbol",
    "expiration",
    "right",
    "strike",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "open_interest",
    "volume",
    "iv",
    "delta",
    "gamma",
    "theta",
    "vega",
)


def _chain_fingerprint(options: list[dict[str, Any]]) -> str:
    """Stable SHA-256 over the actual option surface consumed by candidate design."""
    normalized = []
    for row in options:
        normalized.append({field: row.get(field) for field in _CHAIN_FINGERPRINT_FIELDS})
    normalized.sort(key=lambda row: str(row.get("symbol") or ""))
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _generate_candidates_with_control(
    config,
    prediction: dict[str, Any],
    beta_opportunity: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    optimizer_config=None,
):
    state_prediction, candidates = _generate_state_pq_candidates(
        config,
        prediction,
        beta_opportunity,
        options,
        optimizer_config=optimizer_config,
    )
    control = build_hgb_vertical_candidate(
        state_prediction,
        beta_opportunity,
        options,
    )
    if control is not None:
        payload = dict(control.get("payload") or {})
        payload["role"] = "validated_directional_incumbent"
        payload["competes_with_full_47_family_universe"] = True
        control["payload"] = payload
        candidates.append(control)

    fingerprint = _chain_fingerprint(options)
    for candidate in candidates:
        payload = dict(candidate.get("payload") or {})
        payload["source_option_chain_fingerprint"] = fingerprint
        payload["source_option_chain_contracts"] = len(options)
        payload["policy_version"] = CURRENT_POLICY_VERSION
        candidate["payload"] = payload
    return state_prediction, candidates


def evidence_provenance(
    snapshot: dict[str, Any] | None,
    chain: dict[str, Any] | None,
    *,
    now: datetime,
    maximum_age_seconds: float = 180.0,
) -> dict[str, Any]:
    """Classify whether a trade is untouched forward evidence using a real chain."""
    detail: dict[str, Any] = {
        "evidence_class": _REPLAY_OR_UNVERIFIED,
        "actual_chain": False,
        "reason": "missing_snapshot_or_chain",
        "policy_version": CURRENT_POLICY_VERSION,
        "policy_contract": POLICY_CONTRACT,
    }
    if not snapshot or not chain:
        return detail
    try:
        snapshot_at = engine_module._parse_time(snapshot.get("captured_at"))
        chain_at = engine_module._parse_time(chain.get("captured_at"))
    except (TypeError, ValueError):
        return {**detail, "reason": "invalid_evidence_timestamp"}

    current = now.astimezone(UTC)
    snapshot_age = (current - snapshot_at).total_seconds()
    chain_age = (current - chain_at).total_seconds()
    pair_age = abs((snapshot_at - chain_at).total_seconds())
    snapshot_source = str(snapshot.get("source") or "")
    chain_source = str(chain.get("source") or "")
    snapshot_integrity = str(snapshot.get("integrity") or "")
    chain_integrity = str(chain.get("integrity") or "")
    production_snapshot = snapshot_source == "tradier_production_stream"
    production_chain = chain_source == "tradier"
    fresh = (
        -5.0 <= snapshot_age <= maximum_age_seconds
        and -5.0 <= chain_age <= maximum_age_seconds
        and pair_age <= maximum_age_seconds
    )
    verified = snapshot_integrity == "VERIFIED" and chain_integrity == "VERIFIED"
    evidence_class = (
        _FORWARD_ACTUAL_CHAIN
        if production_snapshot and production_chain and fresh and verified
        else _REPLAY_OR_UNVERIFIED
    )
    reason = (
        "fresh_verified_tradier_snapshot_and_chain"
        if evidence_class == _FORWARD_ACTUAL_CHAIN
        else "not_fresh_verified_production_snapshot_and_chain"
    )
    return {
        "evidence_class": evidence_class,
        "actual_chain": bool(evidence_class == _FORWARD_ACTUAL_CHAIN),
        "reason": reason,
        "policy_version": CURRENT_POLICY_VERSION,
        "policy_contract": POLICY_CONTRACT,
        "classified_at": current.isoformat(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_captured_at": snapshot.get("captured_at"),
        "snapshot_source": snapshot_source,
        "snapshot_integrity": snapshot_integrity,
        "chain_snapshot_id": chain.get("chain_snapshot_id"),
        "chain_captured_at": chain.get("captured_at"),
        "chain_source": chain_source,
        "chain_integrity": chain_integrity,
        "snapshot_age_seconds": snapshot_age,
        "chain_age_seconds": chain_age,
        "snapshot_chain_delta_seconds": pair_age,
    }


engine_module.classify_regime_hierarchy = classify_regime_hierarchy
engine_module.AlphaRegimeLifecycleEngine = AlphaRiskSetLifecycleEngine
engine_module.generate_state_pq_candidates = _generate_candidates_with_control
engine_module.build_agent_plan = build_agent_plan


class V2EngineService(engine_module.V2EngineService):
    """Replay-repaired closed-loop paper trader."""

    def _market_intelligence(self, **kwargs: Any):
        market_state, alpha_regime, lifecycle = super()._market_intelligence(**kwargs)
        snapshot = kwargs["snapshot"]
        stamp = engine_module._parse_time(snapshot["captured_at"])
        session_date = stamp.astimezone(ET).date().isoformat()
        market_state["directional_setup_used_today"] = (
            self.journal.get_control(_DIRECTIONAL_DATE_CONTROL) == session_date
        )
        market_state["lifecycle_authority"] = "alpha_discrete_time_risk_set_survival"
        market_state["policy_version"] = CURRENT_POLICY_VERSION
        return market_state, alpha_regime, lifecycle

    def _preview_fee_gate(self, candidate: dict[str, Any]):
        ok, detail = super()._preview_fee_gate(candidate)
        payload = candidate.get("payload") or {}
        if str(payload.get("authority") or "") != _HGB_AUTHORITY:
            return ok, detail
        if "preview_roundtrip_fee_estimate" not in detail:
            return ok, {
                **detail,
                "legacy_pq_ev_authority": False,
                "preview_policy": "hgb_control_fee_and_risk_only",
            }
        roundtrip = float(detail.get("preview_roundtrip_fee_estimate") or 0.0)
        preview_risk = float(detail.get("preview_max_loss_dollars") or 0.0)
        risk_limit = min(100.0, float(self.config.risk.maximum_trade_risk_dollars))
        fee_risk_ok = roundtrip <= 5.0 and preview_risk <= risk_limit + 1e-9
        return fee_risk_ok, {
            **detail,
            "legacy_pq_ev_authority": False,
            "preview_policy": "hgb_control_fee_and_risk_only",
            "robust_ev_after_preview_fees": None,
        }

    def _annotate_open_position_evidence(self, snapshot: dict[str, Any]) -> None:
        chain, options = self.journal.latest_option_chain("SPY")
        current_fingerprint = _chain_fingerprint(options)
        base_provenance = evidence_provenance(
            snapshot,
            chain,
            now=datetime.now(UTC),
            maximum_age_seconds=max(
                180.0,
                float(self.config.market.option_quote_stale_seconds),
            ),
        )
        with self.journal.transaction() as con:
            rows = con.execute(
                "SELECT position_id,payload_json FROM positions WHERE status='OPEN'"
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                candidate = payload.get("candidate") or {}
                inner = candidate.get("payload") or {}
                if str(inner.get("authority") or "") != _TRADER_AGENT_AUTHORITY:
                    continue
                thesis = inner.get("trade_thesis") or {}
                if not isinstance(thesis, dict) or thesis.get("evidence_provenance"):
                    continue

                candidate_fingerprint = str(inner.get("source_option_chain_fingerprint") or "")
                candidate_policy = str(inner.get("policy_version") or "")
                provenance = dict(base_provenance)
                if provenance.get("evidence_class") == _FORWARD_ACTUAL_CHAIN:
                    if candidate_policy != CURRENT_POLICY_VERSION:
                        provenance.update(
                            {
                                "evidence_class": _REPLAY_OR_UNVERIFIED,
                                "actual_chain": False,
                                "reason": "candidate_policy_version_mismatch",
                                "candidate_policy_version": candidate_policy or None,
                            }
                        )
                    elif not candidate_fingerprint or candidate_fingerprint != current_fingerprint:
                        provenance.update(
                            {
                                "evidence_class": _REPLAY_OR_UNVERIFIED,
                                "actual_chain": False,
                                "reason": "candidate_option_surface_does_not_match_verified_chain",
                                "candidate_option_chain_fingerprint": candidate_fingerprint or None,
                                "verified_option_chain_fingerprint": current_fingerprint,
                            }
                        )
                    else:
                        provenance["candidate_option_chain_fingerprint"] = candidate_fingerprint
                        provenance["verified_option_chain_fingerprint"] = current_fingerprint
                        provenance["source_option_chain_contracts"] = inner.get(
                            "source_option_chain_contracts"
                        )

                thesis["policy_version"] = CURRENT_POLICY_VERSION
                thesis["evidence_provenance"] = provenance
                inner["trade_thesis"] = thesis
                candidate["payload"] = inner
                payload["candidate"] = candidate
                con.execute(
                    "UPDATE positions SET payload_json=? WHERE position_id=? AND status='OPEN'",
                    (self.journal._json(payload), row["position_id"]),
                )

    def run_once(self) -> str | None:
        result = super().run_once()
        snapshot = self.journal.latest_snapshot()
        if snapshot:
            self._annotate_open_position_evidence(snapshot)
            stamp = engine_module._parse_time(snapshot["captured_at"])
            session_date = stamp.astimezone(ET).date().isoformat()
            directional_key = "|".join((session_date, PLAYBOOK_DIRECTIONAL, "FIRST_SETUP"))
            if self.journal.get_control("last_v2_agent_setup_key") == directional_key:
                self.journal.set_control(_DIRECTIONAL_DATE_CONTROL, session_date)
        return result

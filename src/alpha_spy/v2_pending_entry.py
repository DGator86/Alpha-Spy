from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .timeutil import ET


@dataclass(frozen=True)
class PendingEntryDecision:
    action: str
    reason: str


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_pending_entry(
    thesis: dict[str, Any],
    beta: dict[str, Any],
    *,
    now: datetime,
) -> PendingEntryDecision:
    """Keep a valid setup alive until its explicit trigger fires or it expires.

    RELEASE means the old waiting condition is satisfied; Alpha then rebuilds the
    trade from the *current* option chain and market state rather than executing a
    stale candidate. CANCEL abandons the setup. WAIT prevents a new unrelated setup
    from overwriting a still-valid pending thesis.
    """
    try:
        expires = datetime.fromisoformat(str(thesis.get("setup_expires_at") or "").replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=now.tzinfo)
    except ValueError:
        return PendingEntryDecision("CANCEL", "pending_thesis_bad_expiry")
    if now >= expires:
        return PendingEntryDecision("CANCEL", "pending_thesis_expired")

    regime = beta.get("regime_forecast") or {}
    if not isinstance(regime, dict) or not bool(regime.get("definable")):
        return PendingEntryDecision("CANCEL", "pending_regime_became_undefined")
    current = str(regime.get("current_regime") or "UNDEFINED")
    confidence = _num(regime.get("confidence"))
    persistence15 = _num(regime.get("persistence_15"))
    persistence30 = _num(regime.get("persistence_30"))
    successor = str(regime.get("most_likely_successor") or "UNDEFINED")
    entry_regime = str(thesis.get("regime") or "UNDEFINED")
    expected_successor = str(thesis.get("most_likely_successor") or "UNDEFINED")
    entry_mode = str(thesis.get("entry_mode") or "")
    playbook = str(thesis.get("playbook") or "")
    direction = str(thesis.get("direction") or "NEUTRAL")
    hgb = beta.get("hgb_direction") or {}
    hgb_eligible = isinstance(hgb, dict) and bool(hgb.get("eligible"))
    hgb_direction = str(hgb.get("direction") or "") if isinstance(hgb, dict) else ""

    if entry_mode == "WAIT_FOR_BETTER_PRICING":
        local = now.astimezone(ET)
        if current != entry_regime or persistence30 < 0.60:
            return PendingEntryDecision("CANCEL", "pending_range_setup_deteriorated")
        after_window = local.hour > 14 or (local.hour == 14 and local.minute >= 45)
        before_cutoff = local.hour < 15 or (local.hour == 15 and local.minute <= 15)
        if after_window and before_cutoff:
            return PendingEntryDecision("RELEASE", "pending_pricing_window_reached")
        return PendingEntryDecision("WAIT", "pending_waiting_for_pricing_window")

    if entry_mode == "WAIT_FOR_TRANSITION":
        if current == expected_successor and confidence >= 0.45:
            return PendingEntryDecision("RELEASE", "forecast_regime_transition_arrived")
        if successor != expected_successor and _num(regime.get("successor_confidence")) >= 0.45:
            return PendingEntryDecision("CANCEL", "forecast_successor_changed_before_entry")
        return PendingEntryDecision("WAIT", "pending_waiting_for_forecast_transition")

    if entry_mode == "WAIT_FOR_CONFIRMATION":
        if playbook == "DIRECTIONAL_MOMENTUM":
            if persistence15 >= 0.40 and hgb_eligible and hgb_direction == direction:
                return PendingEntryDecision("RELEASE", "directional_confirmation_arrived")
            if hgb_eligible and hgb_direction and hgb_direction != direction:
                return PendingEntryDecision("CANCEL", "directional_confirmation_flipped")
        elif playbook == "MEAN_REVERSION":
            target_regime = "DIRECTIONAL_UP" if direction == "BULLISH" else "DIRECTIONAL_DOWN"
            if (hgb_eligible and hgb_direction == direction) or current == target_regime:
                return PendingEntryDecision("RELEASE", "mean_reversion_confirmation_arrived")
            if current == entry_regime and persistence15 >= 0.72:
                return PendingEntryDecision("CANCEL", "mean_reversion_thesis_lost_to_persistence")
        return PendingEntryDecision("WAIT", "pending_waiting_for_confirmation")

    return PendingEntryDecision("RELEASE", "pending_entry_mode_no_longer_requires_wait")

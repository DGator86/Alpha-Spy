from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

TAKE_PROFIT = "TAKE_PROFIT"
SCALE = "SCALE"
BAIL = "BAIL"
SELL_FOR_LOSS = "SELL_FOR_LOSS"
HOLD = "HOLD"
ADJUST = "ADJUST"
RESTRUCTURE = "RESTRUCTURE"
ADD = "ADD"


@dataclass(frozen=True)
class TradeManagementDecision:
    action: str
    reason: str
    thesis_valid: bool
    should_exit: bool
    scale_quantity: int
    state: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _regime(beta: dict[str, Any] | None) -> dict[str, Any]:
    beta = beta or {}
    raw = beta.get("regime_forecast") or {}
    return raw if isinstance(raw, dict) else {}


def manage_trade(
    thesis: dict[str, Any],
    *,
    elapsed_minutes: float,
    fair_pnl: float,
    liquidation_pnl: float,
    mfe: float,
    quantity: int,
    beta: dict[str, Any] | None,
    current_iv: float | None,
    entry_iv: float | None,
    already_scaled: bool = False,
    already_added: bool = False,
    max_quantity: int | None = None,
) -> TradeManagementDecision:
    """Implement Steps 11-13: reassess thesis, timing, economics and sizing.

    Management uses fair combo P&L for economic invalidation and target logic.
    Liquidation P&L is recorded separately and only becomes realized when an exit is
    actually executed, preventing multi-leg bid/ask width from manufacturing stops.
    Scale-ins are permitted only when evidence strengthens and the trade is not
    losing; the agent never averages down merely because price moved against it.
    """
    regime = _regime(beta)
    current_regime = str(regime.get("current_regime") or "UNDEFINED")
    regime_defined = bool(regime.get("definable"))
    regime_conf = _num(regime.get("confidence"))
    persistence15 = _num(regime.get("persistence_15"))
    persistence30 = _num(regime.get("persistence_30"))
    successor = str(regime.get("most_likely_successor") or "UNDEFINED")
    successor_probs = regime.get("successor_probabilities") or {}
    if not isinstance(successor_probs, dict):
        successor_probs = {}

    playbook = str(thesis.get("playbook") or "")
    direction = str(thesis.get("direction") or "NEUTRAL")
    target1 = _num(thesis.get("first_profit_target_dollars"), 5.0)
    target2 = _num(thesis.get("second_profit_target_dollars"), 15.0)
    stop = _num(thesis.get("stop_loss_dollars"), 10.0)
    expected_time = _num(thesis.get("expected_time_to_profit_minutes"), 15.0)
    time_stop = _num(thesis.get("time_stop_minutes"), expected_time + 5.0)
    entry_regime = str(thesis.get("regime") or "UNDEFINED")
    entry_conf = _num(thesis.get("regime_confidence"))
    entry_persistence15 = _num(thesis.get("persistence_15"))
    entry_persistence30 = _num(thesis.get("persistence_30"))
    entry_successor = str(thesis.get("most_likely_successor") or "UNDEFINED")
    allowed_max_quantity = max(1, int(max_quantity or thesis.get("maximum_quantity") or 1))
    hgb = (beta or {}).get("hgb_direction") or {}
    state = (beta or {}).get("predictive_state") or {}
    pbig15 = _num(state.get("p_big_15")) if isinstance(state, dict) else 0.0

    iv_change = None
    if current_iv is not None and entry_iv is not None and entry_iv > 0:
        iv_change = float(current_iv) - float(entry_iv)

    common = {
        "elapsed_minutes": elapsed_minutes,
        "fair_pnl": fair_pnl,
        "liquidation_pnl": liquidation_pnl,
        "mfe": mfe,
        "quantity": quantity,
        "maximum_quantity": allowed_max_quantity,
        "entry_regime": entry_regime,
        "current_regime": current_regime,
        "regime_defined": regime_defined,
        "entry_regime_confidence": entry_conf,
        "regime_confidence": regime_conf,
        "entry_persistence_15": entry_persistence15,
        "persistence_15": persistence15,
        "entry_persistence_30": entry_persistence30,
        "persistence_30": persistence30,
        "entry_successor": entry_successor,
        "current_successor": successor,
        "successor_probabilities": successor_probs,
        "p_big_15": pbig15,
        "entry_iv": entry_iv,
        "current_iv": current_iv,
        "iv_change": iv_change,
        "target_1": target1,
        "target_2": target2,
        "stop": stop,
        "expected_time_to_profit_minutes": expected_time,
        "time_stop_minutes": time_stop,
        "playbook": playbook,
        "direction": direction,
    }

    def result(action: str, reason: str, valid: bool, *, exit_: bool, scale: int = 0) -> TradeManagementDecision:
        return TradeManagementDecision(
            action=action,
            reason=reason,
            thesis_valid=valid,
            should_exit=exit_,
            scale_quantity=scale,
            state={**common, "action": action, "reason": reason, "thesis_valid": valid},
        )

    if fair_pnl <= -abs(stop):
        return result(SELL_FOR_LOSS, "economic_stop_reached", False, exit_=True)

    if fair_pnl >= target2:
        return result(TAKE_PROFIT, "second_profit_objective_reached", True, exit_=True)

    if fair_pnl >= target1 and quantity >= 2 and not already_scaled:
        return result(SCALE, "first_profit_target_scale_and_protect", True, exit_=False, scale=max(1, quantity // 2))

    if mfe >= target1 and fair_pnl <= max(0.45 * target1, 0.45 * mfe):
        return result(TAKE_PROFIT, "post_target_profit_protection_triggered", True, exit_=True)

    if not regime_defined and elapsed_minutes >= 3.0:
        action = BAIL if fair_pnl >= 0 else SELL_FOR_LOSS
        return result(action, "regime_became_undefined", False, exit_=True)

    hgb_eligible = isinstance(hgb, dict) and bool(hgb.get("eligible"))
    hgb_direction = str(hgb.get("direction") or "") if isinstance(hgb, dict) else ""
    if playbook == "DIRECTIONAL_MOMENTUM":
        opposite = hgb_eligible and hgb_direction and hgb_direction != direction
        opposite_successor = "DIRECTIONAL_DOWN" if direction == "BULLISH" else "DIRECTIONAL_UP"
        opposite_prob = _num(successor_probs.get(opposite_successor))
        if opposite or opposite_prob >= 0.50:
            action = BAIL if fair_pnl >= 0 else SELL_FOR_LOSS
            return result(action, "directional_thesis_flipped", False, exit_=True)
        if persistence15 < 0.25 and elapsed_minutes >= 5.0:
            return result(BAIL, "directional_regime_persistence_collapsed", False, exit_=True)

    elif playbook == "LATE_RANGE_CARRY":
        if current_regime in {"EXPANSION", "DIRECTIONAL_UP", "DIRECTIONAL_DOWN"} and regime_conf >= 0.45:
            action = BAIL if fair_pnl >= 0 else SELL_FOR_LOSS
            return result(action, "range_regime_broke", False, exit_=True)
        if pbig15 >= 0.50:
            return result(BAIL, "large_move_probability_invalidated_range", False, exit_=True)
        if iv_change is not None and iv_change >= 0.05:
            action = BAIL if fair_pnl >= 0 else SELL_FOR_LOSS
            return result(action, "implied_volatility_expanded_five_points", False, exit_=True)

    elif playbook == "VOLATILITY_EXPANSION":
        if pbig15 <= 0.35 and elapsed_minutes >= 5.0:
            action = BAIL if fair_pnl >= 0 else SELL_FOR_LOSS
            return result(action, "expected_expansion_failed_to_materialize", False, exit_=True)
        if iv_change is not None and iv_change <= -0.05 and fair_pnl <= 0:
            return result(SELL_FOR_LOSS, "long_vol_iv_thesis_failed", False, exit_=True)

    elif playbook == "MEAN_REVERSION":
        if current_regime == entry_regime and persistence15 >= 0.65 and elapsed_minutes >= 5.0:
            return result(BAIL, "mean_reversion_failed_regime_persisted", False, exit_=True)

    elif playbook == "REGIME_TRANSITION":
        if successor != entry_successor and regime_conf >= 0.45:
            action = BAIL if fair_pnl >= 0 else SELL_FOR_LOSS
            return result(action, "forecast_successor_regime_changed", False, exit_=True)

    # Scale in only on strengthening evidence, never on a losing trade. Entry is
    # deliberately one unit so the market must confirm before more risk is added.
    strengthening = (
        persistence15 >= entry_persistence15 + 0.08
        or persistence30 >= entry_persistence30 + 0.08
        or regime_conf >= entry_conf + 0.10
    )
    directional_confirmed = playbook != "DIRECTIONAL_MOMENTUM" or (
        hgb_eligible and hgb_direction == direction
    )
    range_confirmed = playbook != "LATE_RANGE_CARRY" or (
        current_regime == "QUIET" and pbig15 <= 0.30 and (iv_change is None or iv_change <= 0.01)
    )
    if (
        not already_added
        and quantity < allowed_max_quantity
        and fair_pnl >= 0.0
        and 2.0 <= elapsed_minutes <= max(5.0, 0.65 * expected_time)
        and strengthening
        and directional_confirmed
        and range_confirmed
    ):
        return result(ADD, "evidence_strengthened_scale_in_without_averaging_down", True, exit_=False, scale=1)

    if elapsed_minutes >= expected_time and fair_pnl <= 0.0:
        return result(SELL_FOR_LOSS, "trade_failed_on_expected_time_to_profit", False, exit_=True)

    if elapsed_minutes >= time_stop:
        action = TAKE_PROFIT if fair_pnl > 0 else SELL_FOR_LOSS
        return result(action, "hard_playbook_time_stop", fair_pnl > 0, exit_=True)

    if current_regime != entry_regime and current_regime != "UNDEFINED" and successor == entry_successor:
        return result(ADJUST, "regime_changed_but_successor_thesis_survives", True, exit_=False)

    return result(HOLD, "thesis_intact_and_trade_on_schedule", True, exit_=False)

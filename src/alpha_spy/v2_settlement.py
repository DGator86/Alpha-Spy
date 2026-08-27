from __future__ import annotations

import json
import os
import statistics
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import httpx

from . import hardening as hardening_module
from .broker_reconcile import BrokerReconciler
from .execution import ExecutionManager, build_multileg_payload
from .hardening import HardenedSettlementService
from .position_management import (
    PositionManagementDecision,
    PositionSignal,
    evaluate_position as legacy_evaluate_position,
)
from .tradier import TradierClient, preview_fees
from .v2_learning import post_trade_review
from .v2_trade_management import ADD, ADJUST, RESTRUCTURE, SCALE, manage_trade

TRADER_AGENT_AUTHORITY = "alpha_v2_closed_loop_trader_agent"
DEFAULT_BETA_V2_STATE_URL = "http://127.0.0.1:8790/api/state"
LEGACY_FIXED_HORIZON_AUTHORITY = "beta_v2_hgb_blocked_walk_forward"


def _candidate_context(position: dict[str, Any]) -> dict[str, Any]:
    payload = position.get("payload") or {}
    candidate = payload.get("candidate") or {}
    inner = candidate.get("payload") or {}
    return inner if isinstance(inner, dict) else {}


def _trade_thesis(position: dict[str, Any]) -> dict[str, Any] | None:
    context = _candidate_context(position)
    thesis = context.get("trade_thesis")
    if str(context.get("authority") or "") != TRADER_AGENT_AUTHORITY:
        return None
    return thesis if isinstance(thesis, dict) else None


def evaluate_v2_position(
    position: dict[str, Any],
    *,
    now: datetime,
    pnl: float,
    mfe: float,
    signal: PositionSignal,
) -> PositionManagementDecision:
    """Compatibility shim for historical fixed-horizon V2 positions/tests."""
    context = _candidate_context(position)
    if str(context.get("authority") or "") != LEGACY_FIXED_HORIZON_AUTHORITY:
        return legacy_evaluate_position(position, now=now, pnl=pnl, mfe=mfe, signal=signal)
    opened = datetime.fromisoformat(str(position["opened_at"]).replace("Z", "+00:00"))
    horizon = max(1, int(context.get("forecast_horizon_minutes") or 15))
    elapsed = max(0.0, (now - opened).total_seconds() / 60.0)
    should_exit = elapsed >= horizon
    return PositionManagementDecision(
        should_exit=should_exit,
        reason="forecast_horizon_exit" if should_exit else None,
        target_pnl=None,
        stop_pnl=None,
        trailing_floor=None,
        thesis_valid=True,
        state={
            "authority": LEGACY_FIXED_HORIZON_AUTHORITY,
            "legacy_compatibility": True,
            "elapsed_minutes": elapsed,
            "forecast_horizon_minutes": horizon,
        },
    )


class V2SettlementService(HardenedSettlementService):
    """Closed-loop minute-by-minute manager for trader-agent positions."""

    def __init__(self, config, journal, *, beta_state_url: str | None = None, **kwargs: Any):
        super().__init__(config, journal, **kwargs)
        self.beta_state_url = beta_state_url or os.getenv("BETA_SPY_STATE_URL") or DEFAULT_BETA_V2_STATE_URL

    @staticmethod
    def _undefined_alpha_lifecycle() -> dict[str, Any]:
        return {
            "definable": False,
            "current_regime": "UNDEFINED",
            "confidence": 0.0,
            "persistence_15": 0.0,
            "persistence_30": 0.0,
            "expected_duration_minutes": 0.0,
            "successor_probabilities": {},
            "most_likely_successor": "UNDEFINED",
            "successor_confidence": 0.0,
            "source": "alpha_lifecycle_state_missing_or_stale",
        }

    def _beta_opportunity(self) -> dict[str, Any] | None:
        try:
            response = httpx.get(self.beta_state_url, timeout=2.5)
            response.raise_for_status()
            state = response.json()
        except (httpx.HTTPError, ValueError):
            state = {}
        opportunity = state.get("v2_opportunity") or state.get("opportunity") if isinstance(state, dict) else None
        if not isinstance(opportunity, dict):
            opportunity = {}
        else:
            try:
                stamp = datetime.fromisoformat(str(opportunity.get("timestamp") or "").replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                age = (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()
                if age < -5.0 or age > 180.0:
                    opportunity = {}
            except ValueError:
                opportunity = {}

        merged = dict(opportunity)
        merged["beta_regime_forecast"] = opportunity.get("regime_forecast")
        merged["regime_forecast"] = self._undefined_alpha_lifecycle()
        merged["regime_authority"] = "alpha_hierarchical_regime"
        merged["lifecycle_authority"] = "alpha_empirical_regime_lifecycle"

        raw = self.journal.get_control("v2_current_agent_market_state")
        if not raw:
            return merged
        try:
            alpha_state = json.loads(raw)
            stamp = datetime.fromisoformat(str(alpha_state.get("timestamp") or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()
        except (TypeError, ValueError, json.JSONDecodeError):
            return merged
        if age < -5.0 or age > 180.0:
            return merged
        regime = alpha_state.get("regime_forecast")
        if isinstance(regime, dict):
            merged["regime_forecast"] = regime
        merged["alpha_regime"] = alpha_state.get("alpha_regime")
        merged["lifecycle"] = alpha_state.get("lifecycle")
        return merged

    @staticmethod
    def _fair_value(position: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> float:
        net = 0.0
        for leg in position.get("legs", []):
            quote = quotes[str(leg["symbol"])]
            quantity = int(leg.get("quantity", 1))
            bid = float(quote.get("bid") or 0.0)
            ask = float(quote.get("ask") or 0.0)
            midpoint = float(quote.get("midpoint") or ((bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0))
            if str(leg["side"]).startswith("buy"):
                net += midpoint * quantity
            else:
                net -= midpoint * quantity
        return abs(net)

    @staticmethod
    def _entry_value(candidate: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> float:
        net_cash = 0.0
        for leg in candidate.get("legs", []):
            quote = quotes[str(leg["symbol"])]
            quantity = int(leg.get("quantity", 1))
            if str(leg["side"]).startswith("buy"):
                net_cash -= float(quote.get("ask") or 0.0) * quantity
            else:
                net_cash += float(quote.get("bid") or 0.0) * quantity
        return abs(net_cash)

    def _pnl_at_value(self, position: dict[str, Any], value: float) -> float:
        entry_kind = position.get("payload", {}).get("entry_kind", "debit")
        quantity = int(position["quantity"])
        entry_fees = float(position.get("payload", {}).get("entry_fees") or 0.0)
        if entry_kind == "debit":
            premium = (value - float(position["entry_value"])) * 100.0 * quantity
        else:
            premium = (float(position["entry_value"]) - value) * 100.0 * quantity
        return premium - entry_fees

    def _current_iv(self, position: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> float | None:
        values = [float(row.get("iv") or 0.0) for row in quotes.values() if float(row.get("iv") or 0.0) > 0]
        if not values:
            _, options = self.journal.latest_option_chain("SPY")
            held = {str(leg.get("symbol") or "") for leg in position.get("legs", [])}
            values = [
                float(row.get("iv") or 0.0)
                for row in options
                if str(row.get("symbol") or "") in held and float(row.get("iv") or 0.0) > 0
            ]
        return float(statistics.median(values)) if values else None

    def _agent_precheck(self, position: dict[str, Any]) -> tuple[Any, float] | None:
        thesis = _trade_thesis(position)
        if thesis is None:
            return None
        quotes, missing = self._held_leg_quotes(position)
        if missing:
            return None
        liquidation_value = HardenedSettlementService._marked_value(position, quotes)
        liquidation_pnl = self._marked_pnl(position, liquidation_value)
        fair_value = self._fair_value(position, quotes)
        fair_pnl = self._pnl_at_value(position, fair_value)
        opened = datetime.fromisoformat(str(position["opened_at"]).replace("Z", "+00:00"))
        elapsed = max(0.0, (datetime.now(UTC) - opened.astimezone(UTC)).total_seconds() / 60.0)
        market_at_entry = thesis.get("market_at_entry") or {}
        entry_iv = market_at_entry.get("atm_iv") if isinstance(market_at_entry, dict) else None
        current_iv = self._current_iv(position, quotes)
        payload = position.get("payload") or {}
        management = manage_trade(
            thesis,
            elapsed_minutes=elapsed,
            fair_pnl=fair_pnl,
            liquidation_pnl=liquidation_pnl,
            mfe=max(float(position.get("mfe") or 0.0), fair_pnl),
            quantity=int(position.get("quantity") or 1),
            beta=self._beta_opportunity(),
            current_iv=current_iv,
            entry_iv=float(entry_iv) if entry_iv not in (None, "") else None,
            already_scaled=bool(payload.get("agent_scaled_once")),
            already_added=bool(payload.get("agent_added_once")),
            max_quantity=int(thesis.get("maximum_quantity") or 1),
        )
        return management, liquidation_value

    def _add_position(self, position: dict[str, Any], management) -> str:
        thesis = _trade_thesis(position) or {}
        candidate = (position.get("payload") or {}).get("candidate") or {}
        current_qty = int(position.get("quantity") or 1)
        maximum_qty = max(1, int(thesis.get("maximum_quantity") or 1))
        add_qty = min(max(1, int(management.scale_quantity or 1)), maximum_qty - current_qty)
        if add_qty <= 0:
            return "add_unavailable_at_max_quantity"

        per_unit_risk = float(thesis.get("per_unit_max_loss_dollars") or candidate.get("max_loss") or 0.0)
        risk_budget = float(thesis.get("risk_budget_dollars") or 0.0)
        if per_unit_risk <= 0.0 or risk_budget <= 0.0 or (current_qty + add_qty) * per_unit_risk > risk_budget + 1e-9:
            return "add_blocked_by_risk_budget"

        quotes, missing = self._held_leg_quotes(position)
        if missing:
            return f"add_missing_quotes:{','.join(missing)}"
        add_price = self._entry_value(candidate, quotes)
        if add_price <= 0.0:
            return "add_invalid_current_entry_price"

        fees = 0.0
        filled_qty = add_qty
        fill_price = add_price
        broker_id = None
        if BrokerReconciler(self.config, self.journal).broker_mode:
            self.config.assert_broker_submission_safe()
            payload = build_multileg_payload(candidate, add_qty, add_price)
            with TradierClient(self.config) as client:
                broker_id, _, preview = self._place_exit(client, payload)
                state = self._wait_for_fill(client, broker_id, self.config.trading.exit_fill_wait_seconds)
            status = ExecutionManager._status(state)
            executed, _ = hardening_module.order_quantities(state)
            if status == "FILLED" and executed <= 0:
                executed = float(add_qty)
            filled_qty = max(0, round(executed))
            if filled_qty <= 0:
                return f"add_not_filled:{status}"
            fill_price = ExecutionManager._average_fill(state) or add_price
            fee_estimate = preview_fees(preview)
            fees = float(fee_estimate or 0.0)
        else:
            leg_contracts = sum(max(1, int(leg.get("quantity", 1))) for leg in candidate.get("legs", []))
            fees = leg_contracts * float(self.config.trading.fee_per_contract) * filled_qty

        new_qty = current_qty + filled_qty
        old_entry = float(position["entry_value"])
        weighted_entry = (old_entry * current_qty + float(fill_price) * filled_qty) / new_qty
        payload = position.setdefault("payload", {})
        payload["agent_added_once"] = True
        payload["added_quantity"] = int(payload.get("added_quantity") or 0) + filled_qty
        payload["add_fees"] = float(payload.get("add_fees") or 0.0) + fees
        payload["entry_fees"] = float(payload.get("entry_fees") or 0.0) + fees
        payload["last_add_broker_order_id"] = str(broker_id) if broker_id is not None else None
        payload["management_state"] = management.state
        total_max_loss = float(candidate.get("max_loss") or per_unit_risk) * new_qty
        total_max_profit = float(candidate.get("max_profit") or position.get("max_profit") or 0.0) * new_qty
        with self.journal.transaction() as con:
            con.execute(
                """
                UPDATE positions
                SET quantity=?,entry_value=?,max_loss=?,max_profit=?,payload_json=?
                WHERE position_id=? AND status='OPEN'
                """,
                (
                    new_qty,
                    weighted_entry,
                    total_max_loss,
                    total_max_profit,
                    self.journal._json(payload),
                    position["position_id"],
                ),
            )
        return f"added={filled_qty} quantity={new_qty} price={float(fill_price):.2f}"

    def _scale_position(self, position: dict[str, Any], management, liquidation_value: float) -> str:
        quantity = int(position.get("quantity") or 1)
        scale_qty = min(max(1, int(management.scale_quantity or 1)), quantity - 1)
        if quantity < 2 or scale_qty <= 0:
            return "scale_unavailable_single_unit"

        payload = position.setdefault("payload", {})
        if BrokerReconciler(self.config, self.journal).broker_mode:
            self.config.assert_broker_submission_safe()
            partial = deepcopy(position)
            partial["quantity"] = scale_qty
            with TradierClient(self.config) as client:
                fill = self._close_as_structure(client, partial, current_value=liquidation_value, force_market=False)
            per_unit_entry = float(position["entry_value"])
            if payload.get("entry_kind", "debit") == "debit":
                realized = (fill.exit_value - per_unit_entry) * 100.0 * scale_qty - fill.fees
            else:
                realized = (per_unit_entry - fill.exit_value) * 100.0 * scale_qty - fill.fees
        else:
            total_liq_pnl = self._marked_pnl(position, liquidation_value)
            realized = total_liq_pnl * scale_qty / quantity

        remaining = quantity - scale_qty
        payload["agent_scaled_once"] = True
        payload["scaled_quantity"] = int(payload.get("scaled_quantity") or 0) + scale_qty
        payload["realized_scale_pnl"] = float(payload.get("realized_scale_pnl") or 0.0) + realized
        payload["management_state"] = management.state
        position["quantity"] = remaining
        position["realized_pnl"] = float(payload["realized_scale_pnl"])
        self.journal.upsert_position(position)
        with self.journal.transaction() as con:
            con.execute(
                "UPDATE positions SET quantity=?,realized_pnl=?,payload_json=? WHERE position_id=?",
                (remaining, position["realized_pnl"], self.journal._json(payload), position["position_id"]),
            )
        return f"scaled={scale_qty} remaining={remaining}"

    def _record_review(self, position_id: str, exit_reason: str | None) -> None:
        with self.journal.session() as con:
            row = con.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone()
        if not row:
            return
        position = dict(row)
        position["legs"] = json.loads(position.pop("legs_json") or "[]")
        position["payload"] = json.loads(position.pop("payload_json") or "{}")
        if position.get("status") != "CLOSED":
            return
        review = post_trade_review(position, exit_reason=exit_reason, journal=self.journal)
        position.setdefault("payload", {})["post_trade_review"] = review
        self.journal.upsert_position(position)

    def _manage_open_position(self) -> str:
        position = self.journal.open_position()
        if not position or _trade_thesis(position) is None:
            return super()._manage_open_position()

        precheck = self._agent_precheck(position)
        if precheck is None:
            return super()._manage_open_position()
        management, liquidation_value = precheck
        position_id = str(position["position_id"])

        if management.action == ADD:
            return self._add_position(position, management)
        if management.action == SCALE and int(position.get("quantity") or 1) >= 2:
            return self._scale_position(position, management, liquidation_value)

        exit_for_adjustment = management.action in {ADJUST, RESTRUCTURE}
        should_exit = bool(management.should_exit or exit_for_adjustment)
        reason = f"agent_{management.action.lower()}:{management.reason}" if should_exit else None

        def evaluate_agent_position(position_arg, *, now, pnl, mfe, signal):
            return PositionManagementDecision(
                should_exit=should_exit,
                reason=reason,
                target_pnl=None,
                stop_pnl=None,
                trailing_floor=None,
                thesis_valid=management.thesis_valid,
                state={
                    **management.state,
                    "evaluated_at": now.isoformat(),
                    "agent_action": management.action,
                    "agent_reason": management.reason,
                    "fair_mark_management": True,
                    "liquidation_mark_reserved_for_execution": True,
                    "regime_authority": "alpha_hierarchical_regime",
                    "lifecycle_authority": "alpha_empirical_regime_lifecycle",
                },
            )

        original = hardening_module.evaluate_position
        hardening_module.evaluate_position = evaluate_agent_position
        try:
            result = super()._manage_open_position()
        finally:
            hardening_module.evaluate_position = original

        if should_exit:
            self._record_review(position_id, reason)
        return result

from __future__ import annotations

import time
import uuid
from typing import Any, ClassVar

from .config import SuiteConfig
from .db import Journal
from .timeutil import utc_iso
from .tradier import (
    TradierClient,
    TradierError,
    order_quantities,
    preview_fees,
)


def build_multileg_payload(
    candidate: dict[str, Any], quantity: int, price: float | None = None
) -> dict[str, Any]:
    legs = candidate.get("legs") or []
    if not 1 <= len(legs) <= 4:
        raise ValueError("Tradier option orders support one to four legs in this suite")
    if len(legs) == 1:
        leg = legs[0]
        payload: dict[str, Any] = {
            "class": "option",
            "symbol": "SPY",
            "option_symbol": leg["symbol"],
            "side": leg["side"],
            "quantity": quantity * int(leg.get("quantity", 1)),
            "duration": "day",
            "type": "limit" if price is not None else "market",
        }
        if price is not None:
            payload["price"] = f"{price:.2f}"
        return payload

    kind = candidate.get("entry_kind", "debit")
    payload = {
        "class": "multileg",
        "symbol": "SPY",
        "duration": "day",
        "type": kind if price is not None else "market",
    }
    if price is not None:
        payload["price"] = f"{price:.2f}"
    for index, leg in enumerate(legs):
        payload[f"option_symbol[{index}]"] = leg["symbol"]
        payload[f"side[{index}]"] = leg["side"]
        payload[f"quantity[{index}]"] = quantity * int(leg.get("quantity", 1))
    return payload


def _leg_contracts(candidate: dict[str, Any]) -> int:
    return sum(max(1, int(leg.get("quantity", 1))) for leg in candidate.get("legs", []))


class ExecutionManager:
    TERMINAL: ClassVar[set[str]] = {"FILLED", "REJECTED", "CANCELED", "EXPIRED", "ERROR"}
    ACTIVE: ClassVar[set[str]] = {
        "OPEN",
        "PENDING",
        "PARTIALLY_FILLED",
        "PENDING_CANCEL",
        "ACCEPTED_FOR_BIDDING",
        "HELD",
        "UNKNOWN",
    }

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal

    @staticmethod
    def _preview_ok(preview: dict[str, Any]) -> bool:
        result = preview.get("result")
        if isinstance(result, bool):
            return result
        status = str(preview.get("status") or preview.get("result") or "").lower()
        return status in {"ok", "success", "accepted", "true"} or bool(preview.get("id"))

    @staticmethod
    def _status(order: dict[str, Any]) -> str:
        return str(order.get("status") or order.get("state") or "UNKNOWN").upper()

    @staticmethod
    def _average_fill(order: dict[str, Any]) -> float | None:
        for key in ("avg_fill_price", "average_fill_price", "fill_price", "last_fill_price"):
            try:
                value = order.get(key)
                if value not in (None, "", 0, 0.0, "0", "0.0"):
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _preview(self, client: TradierClient, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not (self.config.trading.require_preview or self.config.tradier.preview_orders):
            return None
        preview = client.preview_order(payload)
        if not self._preview_ok(preview):
            raise TradierError(f"Order preview failed: {preview}")
        return preview

    def _poll_for(
        self,
        client: TradierClient,
        broker_id: str | int,
        seconds: int,
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        deadline = time.monotonic() + max(1, int(seconds))
        state: dict[str, Any] = {}
        status = "UNKNOWN"
        while time.monotonic() < deadline:
            time.sleep(min(2.0, max(0.25, deadline - time.monotonic())))
            state = client.order(broker_id)
            status = self._status(state)
            executed, remaining = order_quantities(state)
            events.append(
                {
                    "event": "poll",
                    "broker_order_id": str(broker_id),
                    "status": status,
                    "exec_quantity": executed,
                    "remaining_quantity": remaining,
                    "response": state,
                }
            )
            if status in self.TERMINAL or status == "PARTIALLY_FILLED" or executed > 0:
                break
        return state, status

    def _cancel_confirm(
        self,
        client: TradierClient,
        broker_id: str | int,
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        canceled = client.cancel_order(broker_id)
        events.append(
            {"event": "cancel_requested", "broker_order_id": str(broker_id), "response": canceled}
        )
        deadline = time.monotonic() + max(3, self.config.trading.cancel_confirm_seconds)
        state: dict[str, Any] = {}
        status = "PENDING_CANCEL"
        while time.monotonic() < deadline:
            time.sleep(1)
            state = client.order(broker_id)
            status = self._status(state)
            if status in self.TERMINAL:
                break
        if status not in self.TERMINAL:
            raise TradierError(
                f"Order {broker_id} did not reach a terminal state after cancellation"
            )
        events.append(
            {
                "event": "cancel_confirmed",
                "broker_order_id": str(broker_id),
                "status": status,
                "response": state,
            }
        )
        return state, status

    def execute(self, decision: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        requested_quantity = min(1, self.config.risk.maximum_contracts)
        entry_price = round(float(candidate["entry_price"]), 2)
        local_order_id = f"O-{uuid.uuid4().hex[:16]}"
        now = utc_iso()

        if decision["action"] == "PAPER_ORDER":
            fees = _leg_contracts(candidate) * self.config.trading.fee_per_contract * requested_quantity
            order = {
                "local_order_id": local_order_id,
                "decision_id": decision["decision_id"],
                "broker_order_id": None,
                "created_at": now,
                "updated_at": now,
                "environment": "paper",
                "status": "FILLED",
                "order_class": "option" if len(candidate.get("legs", [])) == 1 else "multileg",
                "order_type": candidate.get("entry_kind", "debit"),
                "requested_price": entry_price,
                "average_fill_price": entry_price,
                "quantity": requested_quantity,
                "previewed": False,
                "payload": {
                    "candidate": candidate,
                    "simulated": True,
                    "exec_quantity": requested_quantity,
                    "remaining_quantity": 0,
                    "estimated_fees": fees,
                },
            }
            self.journal.insert_order(order)
            self._open_position(decision, candidate, order, requested_quantity, fees)
            return order

        self.config.assert_broker_submission_safe()
        payload = build_multileg_payload(candidate, requested_quantity, entry_price)
        payload["tag"] = f"{self.config.trading.tag_prefix}-{local_order_id[-8:]}"
        current_price = entry_price
        events: list[dict[str, Any]] = []
        previews: list[dict[str, Any]] = []
        broker_ids: list[str] = []
        order_state: dict[str, Any] = {}
        status = "UNKNOWN"
        active_id: str | int | None = None

        with TradierClient(self.config) as client:
            preview = self._preview(client, payload)
            if preview is not None:
                previews.append(preview)
            response = client.place_order(payload)
            active_id = response.get("id")
            if not active_id:
                raise TradierError(f"Tradier did not return an order id: {response}")
            broker_ids.append(str(active_id))
            events.append(
                {
                    "event": "submitted",
                    "broker_order_id": str(active_id),
                    "price": current_price,
                    "response": response,
                }
            )

            step_count = max(0, int(self.config.trading.limit_price_steps))
            wait_seconds = max(2, int(self.config.trading.limit_price_wait_seconds))
            entry_kind = str(candidate.get("entry_kind") or "debit")
            if len(candidate.get("legs", [])) == 1:
                first_side = str(candidate["legs"][0].get("side") or "buy_to_open")
                direction = 1 if first_side.startswith("buy") else -1
            else:
                direction = 1 if entry_kind == "debit" else -1

            for step in range(step_count + 1):
                order_state, status = self._poll_for(client, active_id, wait_seconds, events)
                executed, remaining = order_quantities(order_state)
                if status in self.TERMINAL:
                    break

                # Once any broker exposure exists, never chase the remainder with a
                # modified/replacement price. Cancel the residual and adopt the fill.
                if status == "PARTIALLY_FILLED" or executed > 0:
                    if remaining > 0:
                        order_state, status = self._cancel_confirm(client, active_id, events)
                    break

                if step >= step_count:
                    break

                current_price = max(0.01, round(current_price + direction * 0.01, 2))
                if payload["class"] == "multileg":
                    # Tradier's change-order endpoint only accepts the single-order
                    # types market/limit/stop/stop_limit. A debit/credit multileg must
                    # therefore be canceled and replaced as an atomic complex order.
                    order_state, status = self._cancel_confirm(client, active_id, events)
                    executed, _ = order_quantities(order_state)
                    if executed > 0:
                        break
                    payload = dict(payload)
                    payload["price"] = f"{current_price:.2f}"
                    replacement_preview = self._preview(client, payload)
                    if replacement_preview is not None:
                        previews.append(replacement_preview)
                    response = client.place_order(payload)
                    active_id = response.get("id")
                    if not active_id:
                        raise TradierError(f"Tradier replacement did not return an order id: {response}")
                    broker_ids.append(str(active_id))
                    events.append(
                        {
                            "event": "replacement_submitted",
                            "broker_order_id": str(active_id),
                            "price": current_price,
                            "response": response,
                        }
                    )
                    status = "UNKNOWN"
                    order_state = {}
                else:
                    changed = client.change_order(
                        active_id,
                        order_type="limit",
                        duration="day",
                        price=current_price,
                    )
                    events.append(
                        {
                            "event": "repriced",
                            "broker_order_id": str(active_id),
                            "price": current_price,
                            "response": changed,
                        }
                    )

            if status not in self.TERMINAL and active_id is not None:
                order_state, status = self._cancel_confirm(client, active_id, events)

        executed, remaining = order_quantities(order_state)
        if status == "FILLED" and executed <= 0:
            executed = float(requested_quantity)
            remaining = 0.0
        average_fill = self._average_fill(order_state)
        if executed > 0 and average_fill is None:
            average_fill = entry_price

        final_preview = previews[-1] if previews else None
        fee_estimate = preview_fees(final_preview)
        if fee_estimate is None:
            fee_estimate = _leg_contracts(candidate) * self.config.trading.fee_per_contract * max(executed, 1.0)
        elif requested_quantity > 0 and 0 < executed < requested_quantity:
            fee_estimate *= executed / requested_quantity

        order = {
            "local_order_id": local_order_id,
            "decision_id": decision["decision_id"],
            "broker_order_id": str(active_id) if active_id is not None else None,
            "created_at": now,
            "updated_at": utc_iso(),
            "environment": self.config.tradier.environment,
            "status": status,
            "order_class": payload["class"],
            "order_type": payload["type"],
            "requested_price": current_price,
            "average_fill_price": average_fill,
            "quantity": requested_quantity,
            "previewed": bool(previews),
            "payload": {
                "request": payload,
                "previews": previews,
                "response": order_state,
                "events": events,
                "broker_order_ids": broker_ids,
                "exec_quantity": executed,
                "remaining_quantity": remaining,
                "estimated_fees": fee_estimate,
            },
        }
        self.journal.insert_order(order)

        filled_quantity = round(executed)
        if filled_quantity > 0:
            self._open_position(decision, candidate, order, filled_quantity, fee_estimate)
            if status != "FILLED":
                self.journal.alert(
                    "critical",
                    "Partial broker exposure adopted",
                    f"Order {active_id} ended {status} after filling {executed}; local position opened for executed exposure",
                    "execution",
                    {"order": order},
                )
        elif status not in {"CANCELED", "REJECTED", "EXPIRED"}:
            self.journal.alert(
                "critical",
                "Broker order requires reconciliation",
                f"Order {active_id} ended with status {status}",
                "execution",
                {"order": order},
            )
        return order

    def _open_position(
        self,
        decision: dict[str, Any],
        candidate: dict[str, Any],
        order: dict[str, Any],
        filled_quantity: int,
        entry_fees: float,
    ) -> None:
        entry_value = float(order.get("average_fill_price") or candidate["entry_price"])
        position = {
            "position_id": f"POS-{uuid.uuid4().hex[:16]}",
            "decision_id": decision["decision_id"],
            "broker_order_id": order.get("broker_order_id"),
            "opened_at": utc_iso(),
            "closed_at": None,
            "status": "OPEN",
            "strategy": candidate["strategy"],
            "quantity": filled_quantity,
            "entry_value": entry_value,
            "current_value": entry_value,
            "realized_pnl": None,
            "unrealized_pnl": -float(entry_fees),
            "max_profit": float(candidate["max_profit"]),
            "max_loss": float(candidate["max_loss"]),
            "mfe": 0.0,
            "mae": min(0.0, -float(entry_fees)),
            "exit_reason": None,
            "legs": candidate.get("legs", []),
            "payload": {
                "candidate": candidate,
                "entry_kind": candidate.get("entry_kind"),
                "entry_order": order.get("local_order_id"),
                "entry_broker_order_ids": order.get("payload", {}).get("broker_order_ids", []),
                "entry_fees": float(entry_fees),
                "management_state": {},
                "broker_reconciliation": "pending" if order.get("environment") != "paper" else "local_paper",
            },
        }
        self.journal.upsert_position(position)

from __future__ import annotations

import time
import uuid
from typing import Any, ClassVar

from .config import SuiteConfig
from .db import Journal
from .timeutil import utc_iso
from .tradier import TradierClient, TradierError


def build_multileg_payload(candidate: dict[str, Any], quantity: int, price: float | None = None) -> dict[str, Any]:
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


class ExecutionManager:
    TERMINAL: ClassVar[set[str]] = {"FILLED", "REJECTED", "CANCELED", "EXPIRED", "ERROR"}

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
                if value not in (None, ""):
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def execute(self, decision: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        quantity = min(1, self.config.risk.maximum_contracts)
        entry_price = round(float(candidate["entry_price"]), 2)
        local_order_id = f"O-{uuid.uuid4().hex[:16]}"
        now = utc_iso()

        if decision["action"] == "PAPER_ORDER":
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
                "quantity": quantity,
                "previewed": False,
                "payload": {"candidate": candidate, "simulated": True},
            }
            self.journal.insert_order(order)
            self._open_position(decision, candidate, order)
            return order

        self.config.assert_live_trading_safe()
        payload = build_multileg_payload(candidate, quantity, entry_price)
        payload["tag"] = f"{self.config.trading.tag_prefix}-{local_order_id[-8:]}"
        preview: dict[str, Any] | None = None
        broker_id: str | int | None = None
        status = "UNKNOWN"
        order_state: dict[str, Any] = {}
        current_price = entry_price
        events: list[dict[str, Any]] = []

        with TradierClient(self.config) as client:
            if self.config.trading.require_preview or self.config.tradier.preview_orders:
                preview = client.preview_order(payload)
                if not self._preview_ok(preview):
                    raise TradierError(f"Order preview failed: {preview}")

            response = client.place_order(payload)
            broker_id = response.get("id")
            if not broker_id:
                raise TradierError(f"Tradier did not return an order id: {response}")
            events.append({"event": "submitted", "price": current_price, "response": response})

            step_count = max(0, int(self.config.trading.limit_price_steps))
            wait_seconds = max(2, int(self.config.trading.limit_price_wait_seconds))
            entry_kind = str(candidate.get("entry_kind") or "debit")
            if len(candidate.get("legs", [])) == 1:
                first_side = str(candidate["legs"][0].get("side") or "buy_to_open")
                direction = 1 if first_side.startswith("buy") else -1
            else:
                direction = 1 if entry_kind == "debit" else -1

            for step in range(step_count + 1):
                deadline = time.monotonic() + wait_seconds
                while time.monotonic() < deadline:
                    time.sleep(2)
                    order_state = client.order(broker_id)
                    status = self._status(order_state)
                    events.append({"event": "poll", "status": status, "response": order_state})
                    if status in self.TERMINAL:
                        break
                if status in self.TERMINAL:
                    break
                if step < step_count:
                    current_price = max(0.01, round(current_price + direction * 0.01, 2))
                    changed = client.change_order(
                        broker_id,
                        order_type=str(payload["type"]),
                        duration="day",
                        price=current_price,
                    )
                    events.append({"event": "repriced", "price": current_price, "response": changed})

            if status not in self.TERMINAL:
                canceled = client.cancel_order(broker_id)
                events.append({"event": "cancel_requested", "response": canceled})
                cancel_deadline = time.monotonic() + max(3, self.config.trading.cancel_confirm_seconds)
                while time.monotonic() < cancel_deadline:
                    time.sleep(1)
                    order_state = client.order(broker_id)
                    status = self._status(order_state)
                    if status in self.TERMINAL:
                        break
                if status not in self.TERMINAL:
                    raise TradierError(
                        f"Order {broker_id} did not reach a terminal state after cancellation"
                    )

        order = {
            "local_order_id": local_order_id,
            "decision_id": decision["decision_id"],
            "broker_order_id": str(broker_id),
            "created_at": now,
            "updated_at": utc_iso(),
            "environment": self.config.tradier.environment,
            "status": status,
            "order_class": payload["class"],
            "order_type": payload["type"],
            "requested_price": current_price,
            "average_fill_price": self._average_fill(order_state),
            "quantity": quantity,
            "previewed": preview is not None,
            "payload": {
                "request": payload,
                "preview": preview,
                "response": order_state,
                "events": events,
            },
        }
        self.journal.insert_order(order)
        if status == "FILLED":
            self._open_position(decision, candidate, order)
        elif status not in {"CANCELED", "REJECTED", "EXPIRED"}:
            self.journal.alert(
                "critical",
                "Broker order requires reconciliation",
                f"Order {broker_id} ended with status {status}",
                "execution",
                {"order": order},
            )
        return order

    def _open_position(self, decision: dict[str, Any], candidate: dict[str, Any], order: dict[str, Any]) -> None:
        entry_value = float(order.get("average_fill_price") or candidate["entry_price"])
        position = {
            "position_id": f"POS-{uuid.uuid4().hex[:16]}",
            "decision_id": decision["decision_id"],
            "broker_order_id": order.get("broker_order_id"),
            "opened_at": utc_iso(),
            "closed_at": None,
            "status": "OPEN",
            "strategy": candidate["strategy"],
            "quantity": int(order["quantity"]),
            "entry_value": entry_value,
            "current_value": entry_value,
            "realized_pnl": None,
            "unrealized_pnl": 0.0,
            "max_profit": float(candidate["max_profit"]),
            "max_loss": float(candidate["max_loss"]),
            "mfe": 0.0,
            "mae": 0.0,
            "exit_reason": None,
            "legs": candidate.get("legs", []),
            "payload": {
                "candidate": candidate,
                "entry_kind": candidate.get("entry_kind"),
                "entry_order": order.get("local_order_id"),
            },
        }
        self.journal.upsert_position(position)


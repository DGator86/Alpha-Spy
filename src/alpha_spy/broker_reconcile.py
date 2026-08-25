from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from .config import SuiteConfig
from .db import Journal
from .timeutil import utc_iso
from .tradier import TradierClient, order_quantities


@dataclass(frozen=True)
class BrokerReconciliationState:
    ok: bool
    blocked: bool
    reason: str
    checked_at: str
    broker_positions: dict[str, float]
    expected_positions: dict[str, float]
    active_managed_orders: list[dict[str, Any]]
    mismatches: dict[str, dict[str, float]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _position_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    output: dict[str, float] = {}
    for row in rows:
        symbol = str(row.get("symbol") or row.get("option_symbol") or "")
        if not symbol:
            continue
        try:
            quantity = float(row.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        output[symbol] = output.get(symbol, 0.0) + quantity
    return output


def expected_position_map(position: dict[str, Any] | None) -> dict[str, float]:
    if not position:
        return {}
    multiplier = max(1, int(position.get("quantity") or 1))
    expected: dict[str, float] = {}
    for leg in position.get("legs", []):
        symbol = str(leg.get("symbol") or "")
        if not symbol:
            continue
        quantity = multiplier * max(1, int(leg.get("quantity", 1)))
        sign = 1.0 if str(leg.get("side") or "").startswith("buy") else -1.0
        expected[symbol] = expected.get(symbol, 0.0) + sign * quantity
    return expected


def _recent_managed_symbols(journal: Journal, prefix: str) -> set[str]:
    symbols: set[str] = set()
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT payload_json
            FROM orders
            WHERE environment <> 'paper'
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            continue
        request = payload.get("request") or {}
        tag = str(request.get("tag") or "")
        if tag and not tag.startswith(prefix):
            continue
        single = request.get("option_symbol")
        if single:
            symbols.add(str(single))
        for index in range(4):
            value = request.get(f"option_symbol[{index}]")
            if value:
                symbols.add(str(value))
    return symbols


class BrokerReconciler:
    ACTIVE_STATUSES: ClassVar[set[str]] = {
        "OPEN",
        "PENDING",
        "PARTIALLY_FILLED",
        "PENDING_CANCEL",
        "ACCEPTED_FOR_BIDDING",
        "HELD",
    }

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal

    @property
    def broker_mode(self) -> bool:
        return bool(
            self.config.trading.enabled
            and self.config.trading.submit_orders
            and self.config.tradier.access_token.get_secret_value()
            and self.config.tradier.account_id
        )

    def check(self, position: dict[str, Any] | None) -> BrokerReconciliationState:
        if not self.broker_mode:
            state = BrokerReconciliationState(
                ok=True,
                blocked=False,
                reason="local_paper_or_submission_disabled",
                checked_at=utc_iso(),
                broker_positions={},
                expected_positions=expected_position_map(position),
                active_managed_orders=[],
                mismatches={},
            )
            self.journal.set_control("broker_reconciliation_state", json.dumps(state.as_dict(), sort_keys=True))
            return state

        with TradierClient(self.config) as client:
            broker_orders = client.orders(include_tags=True, limit=1000)
            broker_positions = _position_map(client.positions())

        prefix = self.config.trading.tag_prefix
        managed_orders: list[dict[str, Any]] = []
        for order in broker_orders:
            tag = str(order.get("tag") or "")
            if not tag.startswith(prefix):
                continue
            status = str(order.get("status") or "").upper()
            executed, remaining = order_quantities(order)
            if status in self.ACTIVE_STATUSES or (executed > 0 and remaining > 0):
                managed_orders.append(
                    {
                        "id": order.get("id"),
                        "tag": tag,
                        "status": status,
                        "exec_quantity": executed,
                        "remaining_quantity": remaining,
                    }
                )

        expected = expected_position_map(position)
        mismatches: dict[str, dict[str, float]] = {}
        if expected:
            for symbol, expected_qty in expected.items():
                actual_qty = float(broker_positions.get(symbol, 0.0))
                if abs(actual_qty - expected_qty) > 1e-9:
                    mismatches[symbol] = {"expected": expected_qty, "actual": actual_qty}
        else:
            # If local state says flat, any broker exposure in a symbol Alpha-SPY
            # recently submitted is unmanaged exposure and must block new risk.
            for symbol in _recent_managed_symbols(self.journal, prefix):
                actual_qty = float(broker_positions.get(symbol, 0.0))
                if abs(actual_qty) > 1e-9:
                    mismatches[symbol] = {"expected": 0.0, "actual": actual_qty}

        reasons = []
        blocking_orders = [
            order
            for order in managed_orders
            if float(order.get("remaining_quantity") or 0.0) > 1e-9
        ]
        if blocking_orders:
            reasons.append("managed_broker_order_still_active")
        if mismatches:
            reasons.append("broker_position_mismatch")
        blocked = bool(reasons)
        state = BrokerReconciliationState(
            ok=not blocked,
            blocked=blocked,
            reason=",".join(reasons) if reasons else "reconciled",
            checked_at=utc_iso(),
            broker_positions=broker_positions,
            expected_positions=expected,
            active_managed_orders=managed_orders,
            mismatches=mismatches,
        )
        self.journal.set_control("broker_reconciliation_state", json.dumps(state.as_dict(), sort_keys=True))
        if blocked:
            self.journal.alert(
                "critical",
                "Broker reconciliation blocked new entries",
                state.reason,
                "reconciliation",
                state.as_dict(),
            )
        return state

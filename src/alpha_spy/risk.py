from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import SuiteConfig
from .db import Journal
from .timeutil import ET, in_et_window, utc_iso, utc_now


@dataclass(frozen=True)
class AccountState:
    equity: float
    cash: float
    buying_power: float
    daily_pnl: float
    valid: bool = True
    source: str = "unknown"
    reason: str = ""


def _first_present(balances: dict[str, Any], *keys: str, default: float) -> float:
    for key in keys:
        if balances.get(key) is not None:
            return _num(balances[key])
    return default


def parse_account_state(payload: dict[str, Any] | None) -> AccountState:
    """Normalize broker balances without treating a missing payload as trustworthy.

    The historical $25k fallback is retained for compatibility/display, but a
    missing/unparseable broker payload is explicitly marked invalid so it can never
    size or authorize a real broker order.
    """
    original = payload
    payload = payload or {}
    balances = payload.get("balances", payload)
    if isinstance(balances, dict) and "balances" in balances:
        balances = balances["balances"] or {}
    if not isinstance(balances, dict):
        balances = {}

    recognized = any(
        balances.get(key) is not None
        for key in (
            "total_equity",
            "equity",
            "total_cash",
            "cash",
            "stock_buying_power",
            "option_buying_power",
            "buying_power",
        )
    )
    equity = _first_present(balances, "total_equity", "equity", "total_cash", default=25_000.0)
    cash = _first_present(balances, "total_cash", "cash", default=equity)
    buying_power = _first_present(
        balances, "option_buying_power", "stock_buying_power", "buying_power", default=cash
    )
    daily_pnl = _first_present(balances, "open_pl", "unrealized_pl", default=0.0)
    valid = bool(recognized and equity >= 0.0 and cash >= 0.0 and buying_power >= 0.0)
    reason = "" if valid else "broker balance payload is missing or incomplete"
    if original is None:
        reason = "broker balance payload is missing"
    return AccountState(
        equity=equity,
        cash=cash,
        buying_power=buying_power,
        daily_pnl=daily_pnl,
        valid=valid,
        source="broker" if valid else "fallback",
        reason=reason,
    )


def health_multiplier(config: SuiteConfig, health_state: str) -> float:
    return {
        "GREEN": 1.0,
        "YELLOW": config.risk.yellow_risk_multiplier,
        "ORANGE": config.risk.orange_risk_multiplier,
        "RED": 0.0,
    }.get(health_state, 0.0)


def allowed_risk(config: SuiteConfig, account: AccountState, trust_score: float, health_state: str) -> float:
    if not account.valid:
        return 0.0
    base = min(
        max(0.0, config.risk.maximum_trade_risk_dollars),
        max(0.0, account.equity * config.risk.account_risk_fraction),
        max(0.0, account.buying_power),
    )
    return max(0.0, base * health_multiplier(config, health_state) * max(0.0, min(1.0, trust_score)))


def trades_today(journal: Journal, now: datetime | None = None) -> int:
    now = now or utc_now()
    session_date = now.astimezone(ET).date().isoformat()
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT created_at FROM decisions
            WHERE action IN ('PAPER_ORDER','SUBMIT_ORDER')
            ORDER BY created_at DESC
            LIMIT 200
            """
        ).fetchall()
    count = 0
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if created.astimezone(ET).date().isoformat() == session_date:
            count += 1
    return count


@dataclass(frozen=True)
class Gate:
    """One entry-gate evaluation.

    ``reason`` is the code published on the decision when this is the first gate
    to fail, so the strings here are the contract the journal and the tests are
    written against. ``detail`` is always populated — a passing gate still has to
    explain what it measured, because the workstation renders the full ladder and
    not only the failure.
    """

    name: str
    label: str
    kind: str
    passed: bool
    reason: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "passed": self.passed,
            "reason": self.reason,
            "detail": self.detail,
        }


def _veto(name: str, label: str, passed: bool, reason: str, detail: str) -> Gate:
    return Gate(name=name, label=label, kind="veto", passed=passed, reason=reason, detail=detail)


def _qualifier(name: str, label: str, passed: bool, reason: str, detail: str) -> Gate:
    return Gate(name=name, label=label, kind="qualifier", passed=passed, reason=reason, detail=detail)


def evaluate_entry_gates(
    config: SuiteConfig,
    journal: Journal,
    feature: dict[str, Any],
    candidates: list[dict[str, Any]],
    account: AccountState,
    *,
    now: datetime | None = None,
) -> tuple[list[Gate], list[dict[str, Any]], float]:
    """Evaluate every entry gate and return them with the surviving candidates.

    The original implementation short-circuited on the first failure, so a
    ``NO_TRADE`` told you one thing that was wrong and nothing about the other
    nine checks. Every gate is evaluated here so the decision record can carry
    the whole ladder; ordering is preserved exactly, because ``choose_decision``
    still reports the first failure as the decision reason.
    """
    now = now or utc_now()
    health_state = str(feature.get("health_state") or "RED")
    trust_score = float(feature.get("trust_score") or 0.0)
    risk = allowed_risk(config, account, trust_score, health_state)
    traded = trades_today(journal, now)
    open_position = journal.open_position()
    loss_limit = abs(config.risk.daily_loss_limit_dollars)

    gates = [
        _veto(
            "operator_paused",
            "Operator pause",
            journal.get_control("entries_paused", "false").lower() != "true",
            "operator_paused",
            "entries paused by operator command"
            if journal.get_control("entries_paused", "false").lower() == "true"
            else "entries enabled",
        ),
        _veto(
            "entry_window",
            "Entry window",
            in_et_window(now, config.risk.entry_start_time_et, config.risk.entry_stop_time_et),
            "outside_entry_window",
            f"{config.risk.entry_start_time_et}–{config.risk.entry_stop_time_et} ET "
            f"(now {now.astimezone(ET).strftime('%H:%M')} ET)",
        ),
        _veto(
            "account_state",
            "Broker account state",
            account.valid,
            "account_state_invalid",
            account.reason or f"{account.source} balances accepted",
        ),
        _veto(
            "buying_power",
            "Buying power",
            account.buying_power > 0,
            "no_buying_power",
            f"${account.buying_power:,.2f} available",
        ),
        _veto(
            "daily_loss_limit",
            "Daily loss limit",
            account.daily_pnl > -loss_limit,
            "daily_loss_limit",
            f"day P&L ${account.daily_pnl:,.2f} against ${-loss_limit:,.2f} limit",
        ),
        _veto(
            "daily_trade_limit",
            "Daily trade limit",
            traded < config.risk.maximum_trades_per_day,
            "daily_trade_limit",
            f"{traded} of {config.risk.maximum_trades_per_day} trades used today",
        ),
        _qualifier(
            "trust_threshold",
            "Audit trust",
            trust_score >= config.risk.minimum_trust_to_trade,
            "trust_below_threshold",
            f"trust {trust_score:.2f} against {config.risk.minimum_trust_to_trade:.2f} floor",
        ),
        _qualifier(
            "health_state",
            "Supervisory health",
            health_state == "GREEN",
            f"health_{health_state.lower()}",
            f"health {health_state}",
        ),
        _qualifier(
            "risk_budget",
            "Risk budget",
            risk > 0,
            "zero_allowed_risk",
            f"${risk:,.2f} allowed against ${config.risk.maximum_trade_risk_dollars:,.2f} base",
        ),
        _veto(
            "no_managed_position",
            "Managed position flat",
            open_position is None,
            "managed_position_already_open",
            f"position {open_position['position_id']} open" if open_position else "flat",
        ),
    ]

    affordable = [
        candidate
        for candidate in candidates
        if candidate.get("status") == "ELIGIBLE"
        and float(candidate.get("max_loss") or 0.0) <= risk + 1e-9
        and float(candidate.get("max_loss") or 0.0) <= account.buying_power + 1e-9
    ]
    eligible_count = sum(1 for c in candidates if c.get("status") == "ELIGIBLE")
    gates.append(
        _qualifier(
            "eligible_structure",
            "Qualified structure",
            eligible_count > 0,
            "no_eligible_candidate",
            f"{eligible_count} of {len(candidates)} ranked structures eligible",
        )
    )
    gates.append(
        _qualifier(
            "structure_within_risk",
            "Structure within risk budget",
            bool(affordable),
            "no_candidate_within_allowed_risk",
            f"{len(affordable)} structure(s) priced at or under ${risk:,.2f}",
        )
    )
    return gates, affordable, risk


def no_trade_decision(
    prediction: dict[str, Any],
    feature: dict[str, Any],
    account: AccountState,
    reason: str,
    *,
    now: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    return {
        "decision_id": f"D-{uuid.uuid4().hex[:16]}",
        "prediction_id": prediction["prediction_id"],
        "candidate_id": None,
        "created_at": utc_iso(now),
        "action": "NO_TRADE",
        "reason": reason,
        "allowed_risk": 0.0,
        "trust_score": float(feature.get("trust_score") or 0.0),
        "health_state": str(feature.get("health_state") or "RED"),
        "payload": {"account": account.__dict__, **(payload or {})},
    }


def choose_decision(
    config: SuiteConfig,
    journal: Journal,
    prediction: dict[str, Any],
    feature: dict[str, Any],
    candidates: list[dict[str, Any]],
    account: AccountState,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    health_state = str(feature["health_state"])
    trust_score = float(feature["trust_score"])
    gates, affordable, risk = evaluate_entry_gates(
        config, journal, feature, candidates, account, now=now
    )

    failed = [gate for gate in gates if not gate.passed]
    chosen: dict[str, Any] | None = None
    if failed:
        action = "NO_TRADE"
        reason = failed[0].reason
    else:
        chosen = max(affordable, key=lambda c: float(c["score"]))
        action = "SUBMIT_ORDER" if config.trading.submit_orders else "PAPER_ORDER"
        reason = "best_risk_eligible_candidate"

    return {
        "decision_id": f"D-{uuid.uuid4().hex[:16]}",
        "prediction_id": prediction["prediction_id"],
        "candidate_id": chosen.get("candidate_id") if chosen else None,
        "created_at": utc_iso(now),
        "action": action,
        "reason": reason,
        "allowed_risk": risk,
        "trust_score": trust_score,
        "health_state": health_state,
        "payload": {
            "candidate": chosen,
            "account": account.__dict__,
            "trades_today": trades_today(journal, now),
            "gates": [gate.as_dict() for gate in gates],
            "failed_gates": [gate.name for gate in failed],
            "considered_candidates": len(candidates),
            "affordable_candidates": len(affordable),
        },
    }


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

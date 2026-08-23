from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import SuiteConfig
from .db import Journal
from .fail_safes import MANAGEMENT_ERROR_FLATTEN
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
    risk = allowed_risk(config, account, trust_score, health_state)
    action = "NO_TRADE"
    reason = "no_eligible_candidate"
    chosen: dict[str, Any] | None = None

    if journal.get_control("entries_paused", "false").lower() == "true":
        reason = "operator_paused"
    elif not in_et_window(now, config.risk.entry_start_time_et, config.risk.entry_stop_time_et):
        reason = "outside_entry_window"
    elif not account.valid:
        reason = "account_state_invalid"
    elif account.buying_power <= 0:
        reason = "no_buying_power"
    elif account.daily_pnl <= -abs(config.risk.daily_loss_limit_dollars):
        reason = "daily_loss_limit"
    elif trades_today(journal, now) >= config.risk.maximum_trades_per_day:
        reason = "daily_trade_limit"
    elif trust_score < config.risk.minimum_trust_to_trade:
        reason = "trust_below_threshold"
    elif health_state != "GREEN":
        reason = f"health_{health_state.lower()}"
    elif risk <= 0:
        reason = "zero_allowed_risk"
    elif (open_position := journal.open_position()) is not None:
        errors = int(open_position.get("payload", {}).get("management_error_count") or 0)
        if errors >= MANAGEMENT_ERROR_FLATTEN:
            journal.set_control("flatten_requested", "true")
            reason = "managed_position_unmanageable"
        else:
            reason = "managed_position_already_open"
    else:
        eligible = [
            c
            for c in candidates
            if c.get("status") == "ELIGIBLE"
            and float(c.get("max_loss") or 0.0) <= risk + 1e-9
            and float(c.get("max_loss") or 0.0) <= account.buying_power + 1e-9
        ]
        if eligible:
            chosen = max(eligible, key=lambda c: float(c["score"]))
            if config.trading.submit_orders:
                action = "SUBMIT_ORDER"
                reason = "best_risk_eligible_candidate"
            else:
                action = "PAPER_ORDER"
                reason = "best_risk_eligible_candidate"
        else:
            reason = "no_candidate_within_allowed_risk"

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
        },
    }


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

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


def parse_account_state(payload: dict[str, Any] | None) -> AccountState:
    payload = payload or {}
    balances = payload.get("balances", payload)
    if isinstance(balances, dict) and "balances" in balances:
        balances = balances["balances"] or {}
    equity = _num(
        balances.get("total_equity")
        or balances.get("equity")
        or balances.get("total_cash")
        or 25_000.0
    )
    cash = _num(balances.get("total_cash") or balances.get("cash") or equity)
    buying_power = _num(
        balances.get("stock_buying_power")
        or balances.get("option_buying_power")
        or balances.get("buying_power")
        or cash
    )
    daily_pnl = _num(
        balances.get("open_pl")
        or balances.get("unrealized_pl")
        or balances.get("day_trade_buying_power_used") * 0.0
        or 0.0
    )
    return AccountState(equity=equity, cash=cash, buying_power=buying_power, daily_pnl=daily_pnl)


def health_multiplier(config: SuiteConfig, health_state: str) -> float:
    return {
        "GREEN": 1.0,
        "YELLOW": config.risk.yellow_risk_multiplier,
        "ORANGE": config.risk.orange_risk_multiplier,
        "RED": 0.0,
    }.get(health_state, 0.0)


def allowed_risk(config: SuiteConfig, account: AccountState, trust_score: float, health_state: str) -> float:
    base = min(
        config.risk.maximum_trade_risk_dollars,
        max(0.0, account.equity * config.risk.account_risk_fraction),
    )
    return max(0.0, base * health_multiplier(config, health_state) * max(0.0, min(1.0, trust_score)))


def trades_today(journal: Journal, now: datetime | None = None) -> int:
    now = now or utc_now()
    session_date = now.astimezone(ET).date().isoformat()
    with journal.connect() as con:
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


def choose_decision(
    config: SuiteConfig,
    journal: Journal,
    prediction: dict[str, Any],
    feature: dict[str, Any],
    candidates: list[dict[str, Any]],
    account: AccountState,
) -> dict[str, Any]:
    now = utc_now()
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
    elif journal.open_position() is not None:
        reason = "managed_position_already_open"
    else:
        eligible = [
            c for c in candidates
            if c.get("status") == "ELIGIBLE"
            and float(c.get("max_loss") or 0.0) <= risk + 1e-9
        ]
        if eligible:
            chosen = max(eligible, key=lambda c: float(c["score"]))
            if config.trading.paper_mode or not config.trading.submit_orders:
                action = "PAPER_ORDER"
                reason = "best_risk_eligible_candidate"
            else:
                action = "SUBMIT_ORDER"
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

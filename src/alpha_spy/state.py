from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import SuiteConfig
from .db import Journal
from .risk import AccountState
from .timeutil import ET, utc_iso, utc_now


def build_dashboard_state(
    config: SuiteConfig,
    journal: Journal,
    account: AccountState | None = None,
) -> dict[str, Any]:
    snapshot = journal.latest_snapshot() or {}
    feature = journal.latest_features() or {}
    prediction = journal.latest_prediction() or {}
    candidates = journal.latest_candidates(prediction.get("prediction_id", ""), 20) if prediction else []
    decision = journal.latest_decision() or {}
    position = journal.open_position()
    metrics = journal.confirmation_metrics(500)
    services = journal.services()
    alerts = journal.recent_alerts(60)
    account = account or AccountState(25_000.0, 25_000.0, 25_000.0, 0.0)
    now = utc_now()
    spy_change_pct = None
    spy_change = None
    if spy_history := journal.quote_history("SPY", 120):
        latest_spy = spy_history[-1]
        spy_change_pct = latest_spy.get("change_pct")
        if len(spy_history) >= 2:
            spy_change = float(latest_spy["price"]) - float(spy_history[-2]["price"])
        elif spy_change_pct is not None and snapshot.get("spy_price"):
            current = float(snapshot["spy_price"])
            spy_change = current - current / max(1.0 + float(spy_change_pct), 1e-9)

    trust = float(feature.get("trust_score") or 0.0)
    health_state = feature.get("health_state") or "RED"
    allowed = min(
        config.risk.maximum_trade_risk_dollars,
        account.equity * config.risk.account_risk_fraction,
    ) * trust

    price_series = [
        {"t": index, "price": float(row["price"]), "timestamp": row["captured_at"]}
        for index, row in enumerate(spy_history)
    ]
    prediction_series = []
    if prediction:
        count = max(len(price_series), 30)
        start = float(prediction["spy_price"])
        mid = float(prediction["predicted_price"])
        low = float(prediction["predicted_low"])
        high = float(prediction["predicted_high"])
        for i in range(count):
            frac = i / max(count - 1, 1)
            prediction_series.append(
                {
                    "t": i,
                    "mid": start + (mid - start) * frac,
                    "low": start + (low - start) * frac,
                    "high": start + (high - start) * frac,
                }
            )

    attribution = feature.get("payload", {}).get("top_attribution", [])
    surface = feature.get("payload", {}).get("surface", {})
    strategy_matrix = []
    for candidate in candidates:
        strategy_matrix.append(
            {
                "strategy": candidate["strategy"],
                "regime": prediction.get("regime", "UNKNOWN"),
                "status": "ENABLED" if candidate["status"] == "ELIGIBLE" else "SHADOW_ONLY",
                "score": candidate["score"],
                "expectancy": candidate["expected_value"],
            }
        )

    position_state: dict[str, Any] = {"open": False}
    if position:
        position_state = {
            "open": True,
            "position_id": position["position_id"],
            "strategy": position["strategy"],
            "description": position["strategy"].replace("_", " "),
            "quantity": position["quantity"],
            "entry_debit": position["entry_value"],
            "current_value": position.get("current_value"),
            "pnl": position.get("unrealized_pnl") or 0.0,
            "pnl_pct": (position.get("unrealized_pnl") or 0.0) / max(position["max_loss"], 1.0),
            "max_loss": position["max_loss"],
            "max_profit": position["max_profit"],
            "mfe": position.get("mfe") or 0.0,
            "mae": position.get("mae") or 0.0,
            "profit_target": 0.50 * position["max_profit"],
            "stop_loss": -0.40 * position["max_loss"],
            "thesis_status": "VALID",
            "exit_recommendation": "MONITOR",
            "opened_at": position["opened_at"],
            "legs": position.get("legs", []),
        }

    service_rows = []
    for service in services:
        try:
            updated = datetime.fromisoformat(service["updated_at"].replace("Z", "+00:00"))
            age_ms = max(0.0, (now - updated).total_seconds() * 1000)
        except Exception:
            age_ms = None
        service_rows.append(
            {
                "name": service["service"],
                "status": service["status"],
                "latency_ms": service.get("latency_ms"),
                "last_event_age_ms": age_ms,
            }
        )

    source = snapshot.get("source", "unknown")
    mode = "LIVE" if config.trading.submit_orders else "PAPER" if config.trading.paper_mode else "OBSERVE"
    return {
        "timestamp": utc_iso(now),
        "engine": {
            "name": "Alpha-SPY",
            "version": "2.0.0",
            "environment": config.tradier.environment.upper(),
            "mode": mode,
        },
        "session": {
            "market_open": snapshot.get("exchange_state") == "open",
            "exchange_time": now.astimezone(ET).strftime("%H:%M:%S"),
            "entry_window": (
                "PAUSED"
                if journal.get_control("entries_paused", "false").lower() == "true"
                else "OPEN"
                if config.risk.entry_start_time_et <= now.astimezone(ET).strftime("%H:%M") <= config.risk.entry_stop_time_et
                else "CLOSED"
            ),
            "forced_flat_time": f"{config.risk.forced_flat_time_et} ET",
        },
        "health": {
            "state": health_state,
            "trust_score": trust,
            "components": {
                "data_integrity": snapshot.get("covered_weight", 0.0),
                "calibration": max(0.0, 1.0 - float(metrics.get("brier") or 0.25)),
                "regime_familiarity": 0.75 if metrics.get("sample_size", 0) >= 100 else 0.50,
                "strategy_reliability": 0.75 if candidates else 0.25,
                "execution_reliability": 1.0 if source == "tradier" else 0.70,
                "model_stability": 0.90,
            },
        },
        "account": {
            "equity": account.equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "daily_pnl": account.daily_pnl,
            "daily_pnl_pct": account.daily_pnl / max(account.equity, 1.0),
            "daily_loss_limit": config.risk.daily_loss_limit_dollars,
            "base_risk": config.risk.maximum_trade_risk_dollars,
            "allowed_risk": allowed,
        },
        "market": {
            "symbol": "SPY",
            "price": snapshot.get("spy_price"),
            "bid": snapshot.get("spy_bid"),
            "ask": snapshot.get("spy_ask"),
            "spread": (snapshot.get("spy_ask") or 0.0) - (snapshot.get("spy_bid") or 0.0),
            "change": spy_change,
            "change_pct": spy_change_pct,
            "predicted_price_15m": prediction.get("predicted_price"),
            "predicted_low_15m": prediction.get("predicted_low"),
            "predicted_high_15m": prediction.get("predicted_high"),
            "probability_up": prediction.get("probability_up"),
            "probability_down": 1.0 - float(prediction.get("probability_up") or 0.5),
            "expected_return_15m": prediction.get("expected_return"),
            "regime": prediction.get("regime", "WAITING"),
            "gamma_state": "UNKNOWN",
            "liquidity_state": "NORMAL" if snapshot.get("integrity") == "VERIFIED" else "DEGRADED",
            "event_state": "NONE",
            "breadth": feature.get("breadth"),
            "pressure": feature.get("weighted_pressure"),
            "concentration": feature.get("concentration"),
            "dispersion": feature.get("dispersion"),
            "correlation": feature.get("correlation"),
            "downside_correlation": feature.get("downside_correlation"),
            "physical_vol": feature.get("realized_vol"),
            "constituent_iv": surface.get("constituent_iv"),
            "spy_iv": surface.get("spy_iv"),
            "vol_gap": surface.get("vol_gap"),
            "skew_gap": surface.get("skew_gap"),
            "assimilation_speed": None,
            "iv_reference_expiration": surface.get("reference_expiration"),
            "iv_coverage": surface.get("covered_weight"),
        },
        "position": position_state,
        "audit": {
            **metrics,
            "vol_mae": None,
            "current_prediction_status": prediction.get("integrity", "WAITING"),
            "t_minus_15_match": prediction.get("integrity", "WAITING"),
        },
        "strategy_matrix": strategy_matrix,
        "challengers": [
            {
                "name": config.prediction.model_version,
                "status": "LIVE",
                "calibration": max(0.0, 1.0 - float(metrics.get("brier") or 0.25)),
                "expectancy": sum(c["expected_value"] for c in candidates[:5]) / max(min(5, len(candidates)), 1),
                "tail_loss": max([c["max_loss"] for c in candidates] or [0.0]),
                "sessions": 0,
            }
        ],
        "services": service_rows,
        "price_series": price_series,
        "prediction_series": prediction_series,
        "attribution": [
            {"cause": "FORECAST_DIRECTION", "count": max(0, int(metrics.get("sample_size", 0) * (1.0 - (metrics.get("direction_accuracy") or 0.0))))},
            {"cause": "RANGE_COVERAGE", "count": max(0, int(metrics.get("sample_size", 0) * (1.0 - (metrics.get("range_coverage") or 0.0))))},
            {"cause": "DATA_INTEGRITY", "count": max(0, int(metrics.get("sample_size", 0) * (1.0 - (metrics.get("integrity_verified_pct") or 0.0))))},
        ],
        "constituent_attribution": attribution,
        "decision": decision,
        "alerts": alerts,
    }

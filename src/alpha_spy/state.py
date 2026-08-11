from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import __version__
from .config import SuiteConfig
from .db import Journal
from .risk import AccountState, allowed_risk
from .timeutil import ET, utc_iso, utc_now


def build_dashboard_state(
    config: SuiteConfig,
    journal: Journal,
    account: AccountState | None = None,
) -> dict[str, Any]:
    snapshot = journal.latest_snapshot() or {}
    feature = journal.latest_features() or {}
    prediction = journal.latest_prediction_for_horizon("15m") or journal.latest_prediction() or {}
    horizon_predictions = journal.latest_predictions_by_horizon()
    validation = journal.latest_validation_run() or {}
    replay = journal.latest_replay_run() or {}
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
    spy_history = journal.quote_history("SPY", 120)
    if spy_history:
        latest_spy = spy_history[-1]
        spy_change_pct = latest_spy.get("change_pct")
        if len(spy_history) >= 2:
            spy_change = float(latest_spy["price"]) - float(spy_history[-2]["price"])
        elif spy_change_pct is not None and snapshot.get("spy_price"):
            current = float(snapshot["spy_price"])
            spy_change = current - current / max(1.0 + float(spy_change_pct), 1e-9)

    trust = float(feature.get("trust_score") or 0.0)
    health_state = feature.get("health_state") or "RED"
    allowed = allowed_risk(config, account, trust, health_state)

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
    hardening = feature.get("payload", {}).get("hardening", {})
    gamma = hardening.get("gamma", {})
    input_health = hardening.get("input_health", {})
    regime_state = prediction.get("payload", {}).get("regime_state", {})
    strategy_matrix = []
    for candidate in candidates:
        strategy_matrix.append(
            {
                "strategy": candidate["strategy"],
                "regime": prediction.get("regime", "UNKNOWN"),
                "status": "ENABLED" if candidate["status"] == "ELIGIBLE" else "SHADOW_ONLY",
                "score": candidate["score"],
                "expectancy": candidate["expected_value"],
                "probability_profit": candidate.get("probability_profit"),
                "max_loss": candidate.get("max_loss"),
                "valuation_method": candidate.get("payload", {}).get("valuation_method"),
                "q_executable_edge": candidate.get("payload", {}).get("q_executable_edge"),
            }
        )

    position_state: dict[str, Any] = {"open": False}
    if position:
        management = position.get("payload", {}).get("management_decision", {})
        management_state = position.get("payload", {}).get("management_state", {})
        position_state = {
            "open": True,
            "position_id": position["position_id"],
            "strategy": position["strategy"],
            "description": position["strategy"].replace("_", " "),
            "quantity": position["quantity"],
            "entry_debit": position["entry_value"],
            "entry_fees": position.get("payload", {}).get("entry_fees", 0.0),
            "current_value": position.get("current_value"),
            "pnl": position.get("unrealized_pnl") or 0.0,
            "pnl_pct": (position.get("unrealized_pnl") or 0.0) / max(position["max_loss"], 1.0),
            "max_loss": position["max_loss"],
            "max_profit": position["max_profit"],
            "mfe": position.get("mfe") or 0.0,
            "mae": position.get("mae") or 0.0,
            "profit_target": management.get("target_pnl"),
            "stop_loss": management.get("stop_pnl"),
            "trailing_floor": management.get("trailing_floor"),
            "thesis_status": "VALID" if management.get("thesis_valid", True) else "INVALID",
            "exit_recommendation": management.get("reason") or "MONITOR",
            "management_state": management_state,
            "broker_reconciliation": position.get("payload", {}).get("broker_reconciliation"),
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

    try:
        reconciliation = json.loads(journal.get_control("broker_reconciliation_state", "{}") or "{}")
    except ValueError:
        reconciliation = {"ok": False, "blocked": True, "reason": "invalid_reconciliation_state"}

    horizon_state = {
        name: {
            "created_at": row.get("created_at"),
            "target_at": row.get("target_at"),
            "role": row.get("payload", {}).get("horizon_role"),
            "expected_return": row.get("expected_return"),
            "probability_up": row.get("probability_up"),
            "predicted_price": row.get("predicted_price"),
            "predicted_low": row.get("predicted_low"),
            "predicted_high": row.get("predicted_high"),
            "sigma_return": row.get("sigma_return"),
            "path": row.get("payload", {}).get("path", {}),
            "distribution": row.get("payload", {}).get("distribution", {}),
            "signal_model": row.get("payload", {}).get("signal_model", {}),
            "shadow_model": row.get("payload", {}).get("shadow_model", {}),
            "model_uncertainty": row.get("payload", {}).get("model_uncertainty"),
            "integrity": row.get("integrity"),
        }
        for name, row in horizon_predictions.items()
    }
    promotion = validation.get("payload", {}) if validation else {}
    replay_state = replay.get("payload", {}) if replay else {}

    source = snapshot.get("source", "unknown")
    if not config.trading.submit_orders:
        mode = "LOCAL_PAPER"
    elif config.tradier.environment == "sandbox":
        mode = "PAPER_BROKER"
    else:
        mode = "LIVE"
    return {
        "timestamp": utc_iso(now),
        "engine": {
            "name": "Alpha-SPY",
            "version": __version__,
            "environment": config.tradier.environment.upper(),
            "mode": mode,
            "market_data_environment": config.tradier.market_environment.upper(),
            "market_stream_enabled": config.tradier.stream_enabled,
        },
        "session": {
            "market_open": snapshot.get("exchange_state") == "open",
            "exchange_time": now.astimezone(ET).strftime("%H:%M:%S"),
            "entry_window": (
                "PAUSED"
                if journal.get_control("entries_paused", "false").lower() == "true"
                else "OPEN"
                if config.risk.entry_start_time_et
                <= now.astimezone(ET).strftime("%H:%M")
                <= config.risk.entry_stop_time_et
                else "CLOSED"
            ),
            "entry_grid_minutes": config.risk.entry_grid_minutes,
            "exit_monitor_seconds": config.risk.exit_monitor_interval_seconds,
            "forced_flat_time": f"{config.risk.forced_flat_time_et} ET",
        },
        "health": {
            "state": health_state,
            "trust_score": trust,
            "input_health": input_health,
            "components": {
                "data_integrity": snapshot.get("covered_weight", 0.0),
                "calibration": max(0.0, 1.0 - float(metrics.get("brier") or 0.25)),
                "regime_familiarity": min(
                    1.0, float(regime_state.get("history_samples") or 0) / 100.0
                ),
                "strategy_reliability": 0.75 if candidates else 0.25,
                "execution_reliability": 1.0 if str(source).startswith("tradier") else 0.70,
                "broker_reconciliation": 1.0 if reconciliation.get("ok", True) else 0.0,
                "model_stability": 0.90,
            },
        },
        "account": {
            "equity": account.equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "daily_pnl": account.daily_pnl,
            "daily_pnl_pct": account.daily_pnl / max(account.equity, 1.0),
            "valid": account.valid,
            "source": account.source,
            "reason": account.reason,
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
            "raw_expected_return_15m": prediction.get("payload", {}).get("raw_expected_return"),
            "signal_model": prediction.get("payload", {}).get("signal_model", {}),
            "option_activity": prediction.get("payload", {}).get("option_activity", hardening.get("option_activity", {})),
            "regime": prediction.get("regime", "WAITING"),
            "regime_state": regime_state,
            "regime_hierarchy": prediction.get("payload", {}).get("regime_hierarchy", {}),
            "market_context": prediction.get("payload", {}).get("market_context", hardening.get("market_context", {})),
            "gamma_state": gamma.get("state", "unknown_gamma"),
            "gamma_proxy": gamma,
            "liquidity_state": "NORMAL" if snapshot.get("integrity") == "VERIFIED" else "DEGRADED",
            "event_state": hardening.get("event_state", "unknown"),
            "event_source": hardening.get("event_source", "unknown"),
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
            "iv_reference_expiration": surface.get("reference_expiration"),
            "iv_coverage": surface.get("covered_weight"),
        },
        "forecast_horizons": horizon_state,
        "position": position_state,
        "broker_reconciliation": reconciliation,
        "promotion": {
            "status": promotion.get("status", validation.get("status") if validation else "NOT_RUN"),
            "validation_id": promotion.get("validation_id", validation.get("validation_id") if validation else None),
            "sessions": promotion.get("sessions", validation.get("sessions") if validation else 0),
            "matured_forecasts": promotion.get("matured_forecasts", validation.get("matured_forecasts") if validation else 0),
            "trades": promotion.get("trades", validation.get("trades") if validation else 0),
            "failed_gates": promotion.get("failed_gates", []),
            "automatic_live_enable": False,
            "report_path": str(config.paths.promotion_report),
        },
        "replay": {
            "status": replay.get("status", "NOT_RUN") if replay else "NOT_RUN",
            "replay_id": replay.get("replay_id") if replay else None,
            "samples": replay.get("samples", 0) if replay else 0,
            "mismatches": replay.get("mismatches", 0) if replay else 0,
            "method": replay_state.get("method"),
        },
        "audit": {
            **metrics,
            "current_prediction_status": prediction.get("integrity", "WAITING"),
            "t_minus_15_match": prediction.get("integrity", "WAITING"),
            "calibration": prediction.get("payload", {}).get("calibration", {}),
        },
        "strategy_matrix": strategy_matrix,
        "challengers": [
            {
                "name": config.prediction.model_version,
                "status": "LIVE",
                "calibration": max(0.0, 1.0 - float(metrics.get("brier") or 0.25)),
                "expectancy": sum(c["expected_value"] for c in candidates[:5])
                / max(min(5, len(candidates)), 1),
                "tail_loss": max([c["max_loss"] for c in candidates] or [0.0]),
                "sessions": 0,
            }
        ],
        "services": service_rows,
        "price_series": price_series,
        "prediction_series": prediction_series,
        "attribution": [
            {
                "cause": "FORECAST_DIRECTION",
                "count": max(
                    0,
                    int(
                        metrics.get("sample_size", 0)
                        * (1.0 - (metrics.get("direction_accuracy") or 0.0))
                    ),
                ),
            },
            {
                "cause": "RANGE_COVERAGE",
                "count": max(
                    0,
                    int(
                        metrics.get("sample_size", 0)
                        * (1.0 - (metrics.get("range_coverage") or 0.0))
                    ),
                ),
            },
            {
                "cause": "DATA_INTEGRITY",
                "count": max(
                    0,
                    int(
                        metrics.get("sample_size", 0)
                        * (1.0 - (metrics.get("integrity_verified_pct") or 0.0))
                    ),
                ),
            },
        ],
        "constituent_attribution": attribution,
        "decision": decision,
        "alerts": alerts,
    }

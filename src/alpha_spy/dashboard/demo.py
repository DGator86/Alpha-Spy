from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Repository


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def base_state(now: datetime, tick: int = 0) -> dict[str, Any]:
    phase = tick / 18.0
    spy = 634.20 + math.sin(phase) * 0.48 + math.sin(phase / 4) * 0.21
    pred = spy + 0.32 + math.sin(phase / 3) * 0.18
    trust = max(0.0, min(1.0, 0.83 + math.sin(phase / 7) * 0.035))
    pnl = 42.0 + math.sin(phase * 1.4) * 17.0
    return {
        "timestamp": iso(now),
        "engine": {"name": "Alpha-SPY", "version": "3.0.0", "environment": "SANDBOX", "mode": "PAPER"},
        "session": {"market_open": True, "exchange_time": now.astimezone().strftime("%H:%M:%S"), "entry_window": "OPEN", "forced_flat_time": "15:55 ET"},
        "health": {
            "state": "GREEN" if trust >= 0.75 else "YELLOW",
            "trust_score": trust,
            "components": {
                "data_integrity": 0.99,
                "calibration": 0.86,
                "regime_familiarity": 0.79,
                "strategy_reliability": 0.91,
                "execution_reliability": 0.94,
                "model_stability": 0.97,
            },
        },
        "account": {
            "equity": 25142.83,
            "cash": 19210.50,
            "buying_power": 38421.00,
            "daily_pnl": pnl,
            "daily_pnl_pct": pnl / 25142.83,
            "daily_loss_limit": 200.0,
            "base_risk": 100.0,
            "allowed_risk": 100.0 * trust,
        },
        "market": {
            "symbol": "SPY",
            "price": spy,
            "change": 1.14,
            "change_pct": 0.0018,
            "bid": spy - 0.01,
            "ask": spy + 0.01,
            "spread": 0.02,
            "predicted_price_15m": pred,
            "predicted_low_15m": spy - 0.54,
            "predicted_high_15m": spy + 0.88,
            "probability_up": 0.67,
            "probability_down": 0.33,
            "expected_return_15m": (pred / spy) - 1,
            "regime": "BROAD BULLISH / NEGATIVE GAMMA",
            "gamma_state": "NEGATIVE",
            "liquidity_state": "NORMAL",
            "event_state": "NONE",
            "breadth": 0.71 + math.sin(phase / 2) * 0.03,
            "pressure": 1.43 + math.sin(phase) * 0.11,
            "concentration": 0.28 + math.cos(phase / 3) * 0.02,
            "dispersion": 0.0086 + math.sin(phase / 5) * 0.0004,
            "correlation": 0.58 + math.sin(phase / 6) * 0.025,
            "downside_correlation": 0.67,
            "physical_vol": 0.181,
            "constituent_iv": 0.196,
            "spy_iv": 0.188,
            "vol_gap": -0.008,
            "skew_gap": -0.014,
            "assimilation_speed": 0.42,
        },
        "position": {
            "open": True,
            "position_id": "DEMO-20260805-001",
            "strategy": "LONG CALL DEBIT SPREAD",
            "description": "SPY 634/636 call debit spread · 0DTE",
            "quantity": 1,
            "entry_debit": 0.78,
            "current_value": 0.78 + pnl / 100.0,
            "pnl": pnl,
            "pnl_pct": pnl / 78.0,
            "max_loss": 78.0,
            "max_profit": 122.0,
            "mfe": 64.0,
            "mae": -11.0,
            "profit_target": 58.0,
            "stop_loss": -33.0,
            "thesis_status": "VALID",
            "exit_recommendation": "HOLD / TRAIL ACTIVE",
            "opened_at": iso(now - timedelta(minutes=11)),
            "legs": [
                {"side": "BUY", "symbol": "SPY260805C00634000", "strike": 634, "type": "CALL", "quantity": 1},
                {"side": "SELL", "symbol": "SPY260805C00636000", "strike": 636, "type": "CALL", "quantity": 1},
            ],
        },
        "audit": {
            "sample_size": 126,
            "direction_accuracy": 0.706,
            "brier": 0.181,
            "range_coverage": 0.794,
            "price_mae": 0.22,
            "vol_mae": 0.009,
            "integrity_verified_pct": 0.984,
            "current_prediction_status": "ON TRACK",
            "t_minus_15_match": "VERIFIED",
        },
        "strategy_matrix": [
            {"strategy": "Long Call", "regime": "Broad bullish trend", "status": "ENABLED", "score": 0.89, "expectancy": 18.4},
            {"strategy": "Long Put", "regime": "Downside correlation expansion", "status": "ENABLED", "score": 0.86, "expectancy": 20.1},
            {"strategy": "Long Strangle", "regime": "Negative gamma expansion", "status": "ENABLED", "score": 0.92, "expectancy": 24.7},
            {"strategy": "Bull Put Spread", "regime": "Positive gamma / bullish", "status": "REDUCED", "score": 0.61, "expectancy": 7.2},
            {"strategy": "Bear Call Spread", "regime": "Positive gamma / bearish", "status": "SHADOW_ONLY", "score": 0.47, "expectancy": -1.1},
            {"strategy": "Iron Condor", "regime": "Stable range / IV rich", "status": "DISABLED", "score": 0.34, "expectancy": -4.6},
        ],
        "challengers": [
            {"name": "Champion v3.0.0", "status": "LIVE", "calibration": 0.89, "expectancy": 16.8, "tail_loss": 48.2, "sessions": 24},
            {"name": "Challenger correlation-v2", "status": "SHADOW", "calibration": 0.91, "expectancy": 18.1, "tail_loss": 46.9, "sessions": 11},
            {"name": "Challenger timing-v1", "status": "SHADOW", "calibration": 0.87, "expectancy": 17.4, "tail_loss": 51.3, "sessions": 7},
        ],
        "services": [
            {"name": "Tradier market stream", "status": "ONLINE", "latency_ms": 84, "last_event_age_ms": 260},
            {"name": "Tradier account poll", "status": "ONLINE", "latency_ms": 112, "last_event_age_ms": 1820},
            {"name": "Prediction engine", "status": "ONLINE", "latency_ms": 41, "last_event_age_ms": 370},
            {"name": "Confirmation tape", "status": "ONLINE", "latency_ms": 96, "last_event_age_ms": 640},
            {"name": "Audit database", "status": "ONLINE", "latency_ms": 6, "last_event_age_ms": 170},
        ],
        "price_series": [
            {"t": i, "price": spy - 0.65 + i * 0.014 + math.sin(i / 6) * 0.14}
            for i in range(90)
        ],
        "prediction_series": [
            {"t": i, "mid": spy - 0.20 + i * 0.007, "low": spy - 0.61 + i * 0.004, "high": spy + 0.27 + i * 0.010}
            for i in range(90)
        ],
        "attribution": [
            {"cause": "FORECAST_DIRECTION", "count": 7, "share": 0.28},
            {"cause": "STRUCTURE_SELECTION", "count": 5, "share": 0.20},
            {"cause": "VOLATILITY", "count": 4, "share": 0.16},
            {"cause": "EXIT_POLICY", "count": 4, "share": 0.16},
            {"cause": "EXECUTION", "count": 3, "share": 0.12},
            {"cause": "DATA", "count": 2, "share": 0.08},
        ],
    }


def seed_history(repo: Repository, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    rng = random.Random(19)
    base = 633.70
    for i in range(96):
        created = now - timedelta(minutes=(96 - i) * 15)
        target = created + timedelta(minutes=15)
        spot = base + i * 0.014 + math.sin(i / 5) * 0.42
        p_up = max(0.08, min(0.92, 0.50 + math.sin(i / 4) * 0.22))
        delta = (p_up - 0.5) * 1.6
        pred = spot + delta
        actual = pred + rng.gauss(0, 0.28)
        record = {
            "prediction_id": f"DEMO-{i:04d}",
            "created_at": iso(created),
            "target_at": iso(target),
            "horizon_minutes": 15,
            "spy_price": spot,
            "predicted_price": pred,
            "predicted_low": pred - 0.58,
            "predicted_high": pred + 0.58,
            "probability_up": p_up,
            "actual_price": actual,
            "direction_correct": (pred >= spot) == (actual >= spot),
            "integrity": "VERIFIED" if i % 19 else "MINOR_REVISION",
            "model_version": "3.0.0-demo",
            "payload": {"actual_high": max(spot, actual) + abs(rng.gauss(0, .12)), "actual_low": min(spot, actual) - abs(rng.gauss(0, .12))},
        }
        repo.upsert_prediction(record)
    repo.add_alert({"timestamp": iso(now - timedelta(minutes=21)), "severity": "warning", "title": "Range calibration watch", "message": "Five-session 80% interval coverage is 76.8%; no control action required.", "source": "audit"})
    repo.add_alert({"timestamp": iso(now - timedelta(minutes=7)), "severity": "info", "title": "T-15 snapshot verified", "message": "The latest confirmation record matched the historical API snapshot within tolerance.", "source": "confirmation"})


async def demo_loop(repo: Repository) -> None:
    tick = 0
    now = datetime.now(UTC)
    if not repo.list_predictions(1):
        seed_history(repo, now)
    while True:
        repo.set_state("live", base_state(datetime.now(UTC), tick), iso(datetime.now(UTC)))
        tick += 1
        await asyncio.sleep(1.0)

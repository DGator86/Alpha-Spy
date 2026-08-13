from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Repository


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


HORIZONS: list[tuple[str, int, str]] = [
    ("5m", 5, "scout"),
    ("15m", 15, "champion"),
    ("30m", 30, "confirmation"),
    ("60m", 60, "context"),
    ("120m", 120, "context"),
    ("eod", 390, "session"),
    ("1d", 1950, "structural"),
]


def _forecast_horizons(now: datetime, spy: float, phase: float) -> dict[str, Any]:
    """Synthetic multi-horizon forecasts with realistic sigma scaling.

    Sigma grows with the square root of horizon length so the ribbon and the
    forecast cone behave the way a real term structure does rather than fanning
    out linearly.
    """
    output: dict[str, Any] = {}
    for index, (name, minutes, role) in enumerate(HORIZONS):
        scale = math.sqrt(minutes / 15.0)
        drift = math.sin(phase / (1.4 + index * 0.5)) * 0.0009 * scale
        sigma = 0.0016 * scale
        mid = spy * (1.0 + drift)
        probability_up = max(0.06, min(0.94, 0.5 + drift / max(sigma, 1e-9) * 0.22))
        output[name] = {
            "created_at": iso(now),
            "target_at": iso(now + timedelta(minutes=minutes)),
            "role": role,
            "horizon_minutes": minutes,
            "expected_return": drift,
            "probability_up": probability_up,
            "predicted_price": mid,
            "predicted_low": mid - spy * sigma * 1.2816,
            "predicted_high": mid + spy * sigma * 1.2816,
            "sigma_return": sigma,
            "distribution": {
                "quantiles": {
                    "p10": mid - spy * sigma * 1.2816,
                    "p25": mid - spy * sigma * 0.6745,
                    "p50": mid,
                    "p75": mid + spy * sigma * 0.6745,
                    "p90": mid + spy * sigma * 1.2816,
                },
                "physical_sigma": sigma,
                "risk_neutral_sigma": sigma * 1.08,
            },
            "signal_model": {"name": "ridge", "authority": "CHAMPION", "signal": drift * 640},
            "shadow_model": {"name": "hgb", "authority": "SHADOW", "signal": drift * 780},
            "model_uncertainty": 0.18 + index * 0.04,
            "integrity": "VERIFIED",
        }
    return output


def _regime_hierarchy(phase: float) -> dict[str, Any]:
    transition = 0.42 + math.sin(phase / 5) * 0.28
    return {
        "micro": {
            "label": "TRENDING_UP",
            "key": "micro=trend_up",
            "history_samples": 148,
            "evidence": "45-minute drift positive, breadth confirming",
        },
        "intraday": {
            "label": "RISK_ON",
            "key": "intraday=risk_on",
            "history_samples": 96,
            "evidence": "240-minute weighted pressure above the risk-on threshold",
        },
        "swing": {
            "label": "NEUTRAL",
            "key": "swing=neutral",
            "history_samples": 61,
            "evidence": "780-minute drift inside the neutral band",
        },
        "structural": {
            "label": "BULLISH",
            "key": "structural=bullish",
            "history_samples": 34,
            "evidence": "1950-minute trend positive with broad participation",
        },
        "transition_risk": max(0.0, min(1.0, transition)),
    }


def _candidates(spy: float, phase: float) -> list[dict[str, Any]]:
    strike = round(spy)
    book = [
        ("CALL_DEBIT_SPREAD", [strike, strike + 2], 0.82, 0.67, 41.0, 19.0, 25.0, 91.0, "ELIGIBLE"),
        ("PUT_CREDIT_SPREAD", [strike - 1, strike - 3], 0.47, 0.73, 29.0, 8.0, 12.0, 77.0, "ELIGIBLE"),
        ("LONG_CALL", [strike + 1], 1.14, 0.52, 16.0, 23.0, -3.0, 61.0, "ELIGIBLE"),
        ("LONG_STRANGLE", [strike - 2, strike + 2], 1.86, 0.41, 4.0, 11.0, -14.0, 38.0, "REJECTED"),
        ("IRON_CONDOR", [strike - 4, strike - 2, strike + 2, strike + 4], 0.61, 0.81, -6.0, -4.0, -19.0, 22.0, "REJECTED"),
    ]
    rows = []
    for index, (strategy, strikes, cost, pop, ev, q_edge, stress, score, status) in enumerate(book):
        wobble = math.sin(phase + index) * 0.04
        width = 2.0 if len(strikes) > 1 else 1.0
        max_loss = cost * 100 if "DEBIT" in strategy or "LONG" in strategy else (width * 100 - cost * 100)
        rows.append(
            {
                "candidate_id": f"C-DEMO-{index:02d}",
                "strategy": strategy,
                "status": status,
                "score": score / 100.0 + wobble,
                "expected_value": ev * (1 + wobble),
                "probability_profit": max(0.02, min(0.98, pop + wobble / 4)),
                "max_loss": max_loss,
                "max_profit": width * 100 - max_loss if max_loss < width * 100 else cost * 100,
                "entry_value": cost,
                "legs": [
                    {
                        "side": "BUY" if leg_index % 2 == 0 else "SELL",
                        "symbol": f"SPY{'C' if 'CALL' in strategy or 'CONDOR' in strategy else 'P'}{int(leg * 1000):08d}",
                        "strike": leg,
                        "type": "CALL" if "CALL" in strategy or "CONDOR" in strategy else "PUT",
                        "quantity": 1,
                    }
                    for leg_index, leg in enumerate(strikes)
                ],
                "rejection_reason": None if status == "ELIGIBLE" else "expected_value_below_floor",
                "valuation_method": "physical_distribution",
                "q_executable_edge": q_edge * (1 + wobble),
                "stress_expected_value": stress,
                "doubled_cost_expected_value": ev * 0.35,
                "breakevens": [strikes[0] + cost] if len(strikes) < 3 else [strikes[0], strikes[-1]],
                "greeks": {"delta": 0.27 + index * 0.05, "gamma": 0.04, "theta": -0.08, "vega": 0.04},
            }
        )
    return rows


def _decision(now: datetime, phase: float, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    # The demo alternates between trading and standing down so both states of
    # the decision panel are reachable without waiting for a real session.
    standing_down = math.sin(phase / 9) < -0.45
    ladder = [
        ("operator_paused", "Operator pause", "veto", True, "entries enabled"),
        ("entry_window", "Entry window", "veto", True, "09:45–15:30 ET (now 11:17 ET)"),
        ("account_state", "Broker account state", "veto", True, "broker balances accepted"),
        ("buying_power", "Buying power", "veto", True, "$38,421.00 available"),
        ("daily_loss_limit", "Daily loss limit", "veto", True, "day P&L $42.00 against $-200.00 limit"),
        ("daily_trade_limit", "Daily trade limit", "veto", True, "1 of 3 trades used today"),
        ("trust_threshold", "Audit trust", "qualifier", True, "trust 0.83 against 0.50 floor"),
        ("health_state", "Supervisory health", "qualifier", True, "health GREEN"),
        ("risk_budget", "Risk budget", "qualifier", True, "$83.00 allowed against $100.00 base"),
        ("no_managed_position", "Managed position flat", "veto", True, "flat"),
        ("eligible_structure", "Qualified structure", "qualifier", True, "3 of 5 ranked structures eligible"),
        (
            "structure_within_risk",
            "Structure within risk budget",
            "qualifier",
            not standing_down,
            "1 structure(s) priced at or under $83.00" if not standing_down else "0 structure(s) priced at or under $83.00",
        ),
    ]
    gates = [
        {
            "name": name,
            "label": label,
            "kind": kind,
            "passed": passed,
            "reason": name,
            "detail": detail,
        }
        for name, label, kind, passed, detail in ladder
    ]
    failed = [gate["name"] for gate in gates if not gate["passed"]]
    best = candidates[0]
    return {
        "decision_id": "D-DEMO-0001",
        "prediction_id": "DEMO-0095",
        "candidate_id": None if failed else best["candidate_id"],
        "created_at": iso(now),
        "action": "NO_TRADE" if failed else "PAPER_ORDER",
        "reason": failed[0] if failed else "best_risk_eligible_candidate",
        "allowed_risk": 83.0,
        "trust_score": 0.83,
        "health_state": "GREEN",
        "gates": gates,
        "failed_gates": failed,
        "candidate": None if failed else best,
        "trades_today": 1,
        "considered_candidates": len(candidates),
        "affordable_candidates": 0 if failed else 1,
    }


def _promotion(now: datetime) -> dict[str, Any]:
    def gate(name: str, passed: bool, actual: Any, threshold: Any) -> dict[str, Any]:
        return {"name": name, "passed": passed, "actual": actual, "threshold": threshold, "detail": ""}

    gates = [
        gate("production_snapshot_fraction", True, 0.997, 0.98),
        gate("required_input_fraction", True, 0.993, 0.98),
        gate("pq_ready_fraction", True, 0.942, 0.90),
        gate("paper_sessions", True, 62, 60),
        gate("matured_forecasts", True, 5418, 5000),
        gate("verified_data", True, 0.989, 0.98),
        gate("15m_sample", True, 811, 750),
        gate("15m_direction", True, 0.548, 0.52),
        gate("15m_direction_wilson_lcb", True, 0.512, 0.505),
        gate("15m_brier", True, 0.243, 0.25),
        gate("15m_brier_skill", True, 0.041, 0.01),
        gate("15m_interval_coverage", True, 0.781, [0.72, 0.88]),
        gate("30m_sample", False, 723, 750),
        gate("30m_direction", True, 0.541, 0.52),
        gate("30m_brier_skill", True, 0.033, 0.01),
        gate("paper_trades", True, 68, 60),
        gate("sandbox_only_trades", True, "all sandbox", "all sandbox"),
        gate("net_pnl", True, 892.0, 0.0),
        gate("profit_factor", True, 1.31, 1.20),
        gate("expectancy_lcb", True, 3.1, 0.0),
        gate("recent_profit_factor", False, 0.96, 1.00),
        gate("max_drawdown", True, 381.0, 600.0),
        gate("doubled_cost_pnl", True, 214.0, 0.0),
        gate("broker_fill_fraction", True, 0.976, 0.95),
        gate("mean_fill_slippage", True, 8.41, 12.0),
        gate("reconciliation_blocks", True, 0, 2),
        gate("modeled_max_loss_holds", True, [], "no loss > 1.15x modeled max"),
        gate("tested_regimes", True, 7, 6),
        gate("regime_coverage", True, 0.83, 0.70),
        gate("deterministic_replay", True, "PASSED", "PASSED"),
    ]
    failed = [g["name"] for g in gates if not g["passed"]]
    return {
        "status": "PAPER_VALIDATION_INCOMPLETE" if failed else "ELIGIBLE_FOR_MANUAL_LIVE_REVIEW",
        "validation_id": "V-DEMO-0007",
        # Anchored to the session start rather than the wall clock: a validation
        # run happens nightly, so a demo that rewrote this every tick would make
        # the section look like it changed on every frame.
        "created_at": iso(now.replace(hour=4, minute=0, second=0, microsecond=0)),
        "sessions": 62,
        "matured_forecasts": 5418,
        "trades": 68,
        "gates": gates,
        "failed_gates": failed,
        "automatic_live_enable": False,
        "report_path": "/var/lib/alpha-spy/validation/promotion.json",
    }


def base_state(now: datetime, tick: int = 0) -> dict[str, Any]:
    phase = tick / 18.0
    spy = 634.20 + math.sin(phase) * 0.48 + math.sin(phase / 4) * 0.21
    pred = spy + 0.32 + math.sin(phase / 3) * 0.18
    trust = max(0.0, min(1.0, 0.83 + math.sin(phase / 7) * 0.035))
    pnl = 42.0 + math.sin(phase * 1.4) * 17.0
    candidates = _candidates(spy, phase)
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
            "regime_hierarchy": _regime_hierarchy(phase),
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
        # Timestamps match what the engine publishes from `quote_history`. The
        # demo omitted them originally, which forced the chart to synthesise a
        # time axis from the wall clock and made the whole series creep right on
        # every frame.
        "price_series": [
            {
                "t": i,
                "timestamp": iso(now - timedelta(minutes=(90 - i))),
                "price": spy - 0.65 + i * 0.014 + math.sin(i / 6) * 0.14,
            }
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
        "forecast_horizons": _forecast_horizons(now, spy, phase),
        "candidates": candidates,
        "decision": _decision(now, phase, candidates),
        "promotion": _promotion(now),
        "replay": {
            "status": "PASSED",
            "replay_id": "R-DEMO-0007",
            "samples": 5418,
            "mismatches": 0,
            "method": "captured_tape_deterministic",
        },
        "broker_reconciliation": {"ok": True, "blocked": False, "reason": "", "checked_at": iso(now)},
        "security": {
            "execution_mode": "PAPER_BROKER",
            "submit_orders": True,
            "paper_mode": True,
            "broker_environment": "sandbox",
            "market_data_environment": "production",
            "production_unlocked": False,
            "production_sentinel": "/etc/alpha-spy/PRODUCTION_UNLOCKED",
            "production_sentinel_present": False,
            "production_approval": "/etc/alpha-spy/PRODUCTION_APPROVED.json",
            "production_approval_present": False,
            "production_approval_valid": False,
            "production_approval_reason": "production approval missing",
            "production_credential_present": False,
            "live_authorization": False,
            "automatic_live_enable": False,
        },
        "constituent_attribution": [
            {"symbol": "NVDA", "contribution": 0.213, "weight": 0.074, "change_pct": 0.0189},
            {"symbol": "AAPL", "contribution": 0.148, "weight": 0.069, "change_pct": 0.0121},
            {"symbol": "MSFT", "contribution": 0.117, "weight": 0.066, "change_pct": 0.0098},
            {"symbol": "META", "contribution": 0.081, "weight": 0.028, "change_pct": 0.0164},
            {"symbol": "AVGO", "contribution": 0.062, "weight": 0.024, "change_pct": 0.0147},
            {"symbol": "AMZN", "contribution": 0.044, "weight": 0.038, "change_pct": 0.0066},
            {"symbol": "JPM", "contribution": -0.038, "weight": 0.014, "change_pct": -0.0154},
            {"symbol": "UNH", "contribution": -0.057, "weight": 0.011, "change_pct": -0.0294},
            {"symbol": "XOM", "contribution": -0.071, "weight": 0.013, "change_pct": -0.0311},
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

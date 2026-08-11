from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from statistics import NormalDist, mean
from typing import Any

import numpy as np

from .config import SuiteConfig
from .db import Journal
from .replay import ReplayVerifier
from .timeutil import ET, utc_iso, utc_now


def _decode(row: Any, field: str = "payload_json") -> dict[str, Any]:
    item = dict(row)
    if field in item:
        item["payload"] = json.loads(item.pop(field) or "{}")
    return item


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if abs(denominator) > 1e-12 else default


def _profit_factor(pnls: list[float]) -> float:
    gains = sum(max(value, 0.0) for value in pnls)
    losses = abs(sum(min(value, 0.0) for value in pnls))
    if losses <= 1e-12:
        return math.inf if gains > 0 else 0.0
    return gains / losses


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += float(pnl)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _wilson_lower(successes: int, n: int, confidence: float) -> float:
    if n <= 0:
        return 0.0
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return max(0.0, (centre - radius) / denom)


def _expectancy_lower(pnls: list[float], confidence: float) -> float:
    if len(pnls) < 2:
        return float("-inf")
    values = np.asarray(pnls, dtype=float)
    z = NormalDist().inv_cdf(confidence)  # one-sided lower confidence bound
    return float(np.mean(values) - z * np.std(values, ddof=1) / math.sqrt(len(values)))


def _brier_skill(rows: list[dict[str, Any]], model_brier: float) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    outcomes = np.asarray([1.0 if float(row.get("actual_return") or 0.0) > 0.0 else 0.0 for row in rows])
    climatology = float(np.mean(outcomes))
    baseline = float(np.mean((outcomes - climatology) ** 2))
    if baseline <= 1e-12:
        return 0.0, baseline
    return float(1.0 - model_brier / baseline), baseline


def _regime_bucket(prediction_payload: dict[str, Any]) -> str:
    state = prediction_payload.get("regime_state", {}) if isinstance(prediction_payload, dict) else {}
    return "|".join(
        str(state.get(key) or "unknown")
        for key in ("volatility", "correlation", "breadth", "dealer_gamma")
    )


def _gate(name: str, passed: bool, actual: Any, threshold: Any, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "threshold": threshold,
        "detail": detail,
    }


class PromotionEvaluator:
    """Objective paper-to-live evidence gate.

    Passing this evaluator does *not* enable production execution.  It means the
    current model/config has accumulated enough synchronized real-time-data +
    sandbox-execution evidence to be eligible for a separate manual live review.
    The production sentinel and evidence-bound approval remain independent locks.
    """

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal

    def _forecast_rows(self) -> list[dict[str, Any]]:
        with self.journal.session() as con:
            rows = con.execute(
                """
                SELECT p.*, o.confirmed_at, o.actual_return, o.direction_correct,
                       o.interval_covered, o.brier, o.integrity AS outcome_integrity,
                       o.price_error, s.source AS snapshot_source,
                       s.integrity AS snapshot_integrity
                FROM predictions p
                JOIN prediction_outcomes o ON o.prediction_id=p.prediction_id
                JOIN market_snapshots s ON s.snapshot_id=p.snapshot_id
                WHERE p.model_version=? AND p.config_hash=?
                ORDER BY p.created_at ASC
                """,
                (self.config.prediction.model_version, self.config.decision_fingerprint()),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return output

    def _trade_rows(self) -> list[dict[str, Any]]:
        with self.journal.session() as con:
            rows = con.execute(
                """
                SELECT pos.*, d.prediction_id, p.regime, p.payload_json AS prediction_payload_json,
                       o.environment AS entry_environment, o.status AS entry_order_status,
                       o.requested_price AS order_requested_price,
                       o.average_fill_price AS order_average_fill_price,
                       o.payload_json AS order_payload_json
                FROM positions pos
                JOIN decisions d ON d.decision_id=pos.decision_id
                JOIN predictions p ON p.prediction_id=d.prediction_id
                LEFT JOIN orders o ON o.broker_order_id=pos.broker_order_id
                WHERE p.model_version=? AND p.config_hash=? AND pos.status='CLOSED'
                ORDER BY pos.closed_at ASC
                """,
                (self.config.prediction.model_version, self.config.decision_fingerprint()),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            item["prediction_payload"] = json.loads(item.pop("prediction_payload_json") or "{}")
            item["order_payload"] = json.loads(item.pop("order_payload_json") or "{}")
            output.append(item)
        return output

    def _broker_order_metrics(self) -> dict[str, Any]:
        with self.journal.session() as con:
            rows = con.execute(
                """
                SELECT o.*, d.action, p.model_version
                FROM orders o
                JOIN decisions d ON d.decision_id=o.decision_id
                JOIN predictions p ON p.prediction_id=d.prediction_id
                WHERE p.model_version=? AND p.config_hash=? AND d.action='SUBMIT_ORDER'
                ORDER BY o.created_at ASC
                """,
                (self.config.prediction.model_version, self.config.decision_fingerprint()),
            ).fetchall()
        broker = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            broker.append(item)
        attempts = len(broker)
        filled_attempts = 0
        slippage: list[float] = []
        non_sandbox = 0
        for order in broker:
            payload = order.get("payload", {})
            executed = payload.get("exec_quantity")
            try:
                executed_f = float(executed) if executed is not None else 0.0
            except (TypeError, ValueError):
                executed_f = 0.0
            if str(order.get("status") or "").upper() == "FILLED" or executed_f > 0:
                filled_attempts += 1
            if str(order.get("environment") or "") != "sandbox":
                non_sandbox += 1
            requested = order.get("requested_price")
            fill = order.get("average_fill_price")
            if requested is not None and fill is not None:
                with suppress(TypeError, ValueError):
                    slippage.append(
                        abs(float(fill) - float(requested))
                        * 100.0
                        * max(int(order.get("quantity") or 1), 1)
                    )
        return {
            "attempts": attempts,
            "filled_attempts": filled_attempts,
            "fill_fraction": _safe_div(filled_attempts, attempts, default=0.0),
            "mean_fill_slippage_dollars": mean(slippage) if slippage else 0.0,
            "non_sandbox_orders": non_sandbox,
        }

    def _reconciliation_blocks(self, first_trade_at: str | None) -> int:
        if not first_trade_at:
            return 0
        with self.journal.session() as con:
            row = con.execute(
                """
                SELECT COUNT(*) AS n FROM alerts
                WHERE timestamp>=? AND source='reconciliation'
                  AND severity IN ('critical','error')
                """,
                (first_trade_at,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def run(self, *, run_replay: bool = True, replay_samples: int | None = None) -> dict[str, Any]:
        forecasts = self._forecast_rows()
        trades = self._trade_rows()
        broker = self._broker_order_metrics()
        v = self.config.validation

        sessions = sorted(
            {
                datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
                .astimezone(ET)
                .date()
                .isoformat()
                for row in forecasts
            }
        )
        verified = [row for row in forecasts if row.get("outcome_integrity") == "VERIFIED"]
        verified_fraction = _safe_div(len(verified), len(forecasts), default=0.0)

        horizon_metrics: dict[str, Any] = {}
        grouped_forecasts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in forecasts:
            name = str(row.get("payload", {}).get("horizon_name") or f"{row['horizon_minutes']}m")
            grouped_forecasts[name].append(row)
        for name, all_rows in grouped_forecasts.items():
            # Primary calibration gates use non-overlapping formal anchors. The
            # minute-by-minute forecasts remain useful for replay/monitoring but
            # are serially dependent and cannot inflate statistical evidence.
            rows = (
                [row for row in all_rows if bool(row.get("formal_anchor"))]
                if name in {"15m", "30m"}
                else all_rows
            )
            n = len(rows)
            successes = sum(int(row["direction_correct"]) for row in rows)
            model_brier = _safe_div(sum(float(row["brier"]) for row in rows), n)
            brier_skill, baseline_brier = _brier_skill(rows, model_brier)
            shadow_rows = [
                row for row in rows
                if str(row.get("payload", {}).get("shadow_model", {}).get("mode") or "")
                == "walk_forward_hist_gradient_boosting_shadow"
                and row.get("payload", {}).get("shadow_model", {}).get("expected_return") is not None
            ]
            shadow_correct = sum(
                (float(row["payload"]["shadow_model"]["expected_return"]) >= 0.0)
                == (float(row.get("actual_return") or 0.0) >= 0.0)
                for row in shadow_rows
            )
            horizon_metrics[name] = {
                "samples": n,
                "all_matured_samples": len(all_rows),
                "sampling": "non_overlapping_formal_anchors" if name in {"15m", "30m"} else "all",
                "direction_accuracy": _safe_div(successes, n),
                "direction_wilson_lcb": _wilson_lower(successes, n, v.confidence_level),
                "brier": model_brier,
                "brier_baseline": baseline_brier,
                "brier_skill": brier_skill,
                "interval_coverage": _safe_div(sum(int(row["interval_covered"]) for row in rows), n),
                "price_mae": _safe_div(sum(abs(float(row["price_error"])) for row in rows), n),
                "verified_fraction": _safe_div(sum(row.get("outcome_integrity") == "VERIFIED" for row in rows), n),
                "trained_signal_fraction": _safe_div(
                    sum(
                        str(row.get("payload", {}).get("signal_model", {}).get("mode") or "")
                        == "walk_forward_ridge"
                        for row in rows
                    ),
                    n,
                ),
                "shadow_challenger": {
                    "samples": len(shadow_rows),
                    "direction_accuracy": _safe_div(shadow_correct, len(shadow_rows)),
                    "trading_authority": False,
                    "affects_promotion": False,
                },
            }

        production_source_fraction = _safe_div(
            sum(
                str(row.get("snapshot_source") or "") == "tradier_production_stream"
                and str(row.get("snapshot_integrity") or "") == "VERIFIED"
                for row in forecasts
            ),
            len(forecasts),
        )
        required_input_fraction = _safe_div(
            sum(bool(row.get("payload", {}).get("input_health", {}).get("required_ok")) for row in forecasts),
            len(forecasts),
        )
        pq_ready_fraction = _safe_div(
            sum(
                row.get("payload", {}).get("distribution", {}).get("p_source")
                == "constituent_student_t_dynamic_covariance"
                and float(row.get("payload", {}).get("distribution", {}).get("q_surface_covered_weight") or 0.0)
                >= self.config.prediction.minimum_pq_surface_weight
                for row in forecasts
            ),
            len(forecasts),
        )

        pnls = [float(row.get("realized_pnl") or 0.0) for row in trades]
        net_pnl = float(sum(pnls))
        profit_factor = float(_profit_factor(pnls))
        max_drawdown = float(_max_drawdown(pnls))
        worst_trade = min(pnls) if pnls else 0.0
        expectancy_lcb = _expectancy_lower(pnls, v.confidence_level)
        recent_pnls = pnls[-min(len(pnls), v.recent_trade_window):]
        recent_net_pnl = float(sum(recent_pnls))
        recent_profit_factor = float(_profit_factor(recent_pnls))

        doubled_cost_pnls: list[float] = []
        loss_ratio_violations: list[dict[str, Any]] = []
        regime_pnls: dict[str, list[float]] = defaultdict(list)
        sandbox_trade_count = 0
        for row, pnl in zip(trades, pnls, strict=True):
            candidate = row.get("payload", {}).get("candidate", {})
            cp = candidate.get("payload", {}) if isinstance(candidate, dict) else {}
            extra_cost = float(cp.get("roundtrip_fees_dollars") or 0.0)
            extra_cost += (
                float(cp.get("combined_spread") or 0.0)
                * 100.0
                * max(self.config.trading.slippage_fraction_of_spread, 0.0)
                * max(int(row.get("quantity") or 1), 1)
            )
            doubled_cost_pnls.append(pnl - extra_cost)
            model_loss = float(row.get("max_loss") or 0.0)
            realized_loss = max(-pnl, 0.0)
            if model_loss > 0 and realized_loss > model_loss * v.maximum_realized_loss_to_model_ratio:
                loss_ratio_violations.append(
                    {
                        "position_id": row.get("position_id"),
                        "realized_loss": realized_loss,
                        "model_max_loss": model_loss,
                        "ratio": realized_loss / model_loss,
                    }
                )
            regime_pnls[_regime_bucket(row.get("prediction_payload", {}))].append(pnl)
            if str(row.get("entry_environment") or "") == "sandbox":
                sandbox_trade_count += 1

        doubled_cost_pnl = float(sum(doubled_cost_pnls))
        regime_report = {
            key: {
                "trades": len(values),
                "net_pnl": float(sum(values)),
                "expectancy": float(mean(values)),
                "profit_factor": float(_profit_factor(values)),
            }
            for key, values in sorted(regime_pnls.items())
        }
        tested_regimes = {key: data for key, data in regime_report.items() if data["trades"] >= v.minimum_regime_trades}
        covered_regime_trades = sum(int(data["trades"]) for data in tested_regimes.values())
        regime_coverage = _safe_div(covered_regime_trades, len(trades), default=0.0)
        negative_tested_regimes = [key for key, data in tested_regimes.items() if float(data["expectancy"]) < 0.0]

        first_trade_at = str(trades[0]["opened_at"]) if trades else None
        reconciliation_blocks = self._reconciliation_blocks(first_trade_at)

        replay = ReplayVerifier(self.config, self.journal).run(replay_samples) if run_replay else None
        if replay is None:
            with self.journal.session() as con:
                row = con.execute("SELECT * FROM replay_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            replay = _decode(row) if row else None

        m15 = horizon_metrics.get("15m", {})
        m30 = horizon_metrics.get("30m", {})
        gates = [
            _gate(
                "paper_execution_mode",
                self.config.trading.enabled
                and self.config.trading.submit_orders
                and self.config.tradier.environment == "sandbox"
                and self.config.trading.paper_mode,
                {
                    "enabled": self.config.trading.enabled,
                    "submit_orders": self.config.trading.submit_orders,
                    "environment": self.config.tradier.environment,
                    "paper_mode": self.config.trading.paper_mode,
                },
                {"enabled": True, "submit_orders": True, "environment": "sandbox", "paper_mode": True},
            ),
            _gate(
                "production_realtime_feed",
                self.config.tradier.market_environment == "production"
                and self.config.tradier.stream_enabled
                and bool(self.config.tradier.market_access_token.get_secret_value()),
                {
                    "environment": self.config.tradier.market_environment,
                    "stream": self.config.tradier.stream_enabled,
                    "token_configured": bool(self.config.tradier.market_access_token.get_secret_value()),
                },
                {"environment": "production", "stream": True, "token_configured": True},
            ),
            _gate("production_snapshot_fraction", production_source_fraction >= v.minimum_verified_data_fraction, production_source_fraction, v.minimum_verified_data_fraction),
            _gate("required_input_fraction", required_input_fraction >= v.minimum_verified_data_fraction, required_input_fraction, v.minimum_verified_data_fraction),
            _gate("pq_ready_fraction", pq_ready_fraction >= 0.90, pq_ready_fraction, 0.90),
            _gate("paper_sessions", len(sessions) >= v.minimum_paper_sessions, len(sessions), v.minimum_paper_sessions),
            _gate("matured_forecasts", len(forecasts) >= v.minimum_matured_forecasts, len(forecasts), v.minimum_matured_forecasts),
            _gate("verified_data", verified_fraction >= v.minimum_verified_data_fraction, verified_fraction, v.minimum_verified_data_fraction),
            _gate("15m_sample", int(m15.get("samples") or 0) >= v.minimum_primary_horizon_samples, int(m15.get("samples") or 0), v.minimum_primary_horizon_samples),
            _gate("15m_trained_signal", float(m15.get("trained_signal_fraction") or 0.0) >= v.minimum_trained_primary_fraction, m15.get("trained_signal_fraction"), v.minimum_trained_primary_fraction),
            _gate("15m_direction", float(m15.get("direction_accuracy") or 0.0) >= v.minimum_direction_accuracy_15m, m15.get("direction_accuracy"), v.minimum_direction_accuracy_15m),
            _gate("15m_direction_wilson_lcb", float(m15.get("direction_wilson_lcb") or 0.0) >= v.minimum_direction_wilson_lcb_15m, m15.get("direction_wilson_lcb"), v.minimum_direction_wilson_lcb_15m),
            _gate("15m_brier", float(m15.get("brier") or 1.0) <= v.maximum_brier_15m, m15.get("brier"), v.maximum_brier_15m),
            _gate("15m_brier_skill", float(m15.get("brier_skill") or -1.0) >= v.minimum_brier_skill_15m, m15.get("brier_skill"), v.minimum_brier_skill_15m),
            _gate("15m_interval_coverage", v.minimum_interval_coverage <= float(m15.get("interval_coverage") or 0.0) <= v.maximum_interval_coverage, m15.get("interval_coverage"), [v.minimum_interval_coverage, v.maximum_interval_coverage]),
            _gate("30m_sample", int(m30.get("samples") or 0) >= v.minimum_primary_horizon_samples, int(m30.get("samples") or 0), v.minimum_primary_horizon_samples),
            _gate("30m_trained_signal", float(m30.get("trained_signal_fraction") or 0.0) >= v.minimum_trained_primary_fraction, m30.get("trained_signal_fraction"), v.minimum_trained_primary_fraction),
            _gate("30m_direction", float(m30.get("direction_accuracy") or 0.0) >= v.minimum_direction_accuracy_30m, m30.get("direction_accuracy"), v.minimum_direction_accuracy_30m),
            _gate("30m_direction_wilson_lcb", float(m30.get("direction_wilson_lcb") or 0.0) >= v.minimum_direction_wilson_lcb_30m, m30.get("direction_wilson_lcb"), v.minimum_direction_wilson_lcb_30m),
            _gate("30m_brier", float(m30.get("brier") or 1.0) <= v.maximum_brier_30m, m30.get("brier"), v.maximum_brier_30m),
            _gate("30m_brier_skill", float(m30.get("brier_skill") or -1.0) >= v.minimum_brier_skill_30m, m30.get("brier_skill"), v.minimum_brier_skill_30m),
            _gate("30m_interval_coverage", v.minimum_interval_coverage <= float(m30.get("interval_coverage") or 0.0) <= v.maximum_interval_coverage, m30.get("interval_coverage"), [v.minimum_interval_coverage, v.maximum_interval_coverage]),
            _gate("paper_trades", len(trades) >= v.minimum_paper_trades, len(trades), v.minimum_paper_trades),
            _gate("sandbox_only_trades", sandbox_trade_count == len(trades) and broker["non_sandbox_orders"] == 0, {"sandbox_closed_trades": sandbox_trade_count, "closed_trades": len(trades), "non_sandbox_orders": broker["non_sandbox_orders"]}, "all sandbox"),
            _gate("net_pnl", net_pnl >= v.minimum_net_pnl, net_pnl, v.minimum_net_pnl),
            _gate("profit_factor", profit_factor >= v.minimum_profit_factor, profit_factor, v.minimum_profit_factor),
            _gate("expectancy_lcb", expectancy_lcb >= v.minimum_expectancy_lcb_dollars, expectancy_lcb, v.minimum_expectancy_lcb_dollars, f"one-sided {v.confidence_level:.0%} lower bound"),
            _gate("recent_net_pnl", len(recent_pnls) >= v.recent_trade_window and recent_net_pnl >= v.minimum_recent_net_pnl, {"samples": len(recent_pnls), "net_pnl": recent_net_pnl}, {"samples": v.recent_trade_window, "minimum_net_pnl": v.minimum_recent_net_pnl}),
            _gate("recent_profit_factor", len(recent_pnls) >= v.recent_trade_window and recent_profit_factor >= v.minimum_recent_profit_factor, {"samples": len(recent_pnls), "profit_factor": recent_profit_factor}, {"samples": v.recent_trade_window, "minimum_profit_factor": v.minimum_recent_profit_factor}),
            _gate("max_drawdown", max_drawdown <= v.maximum_drawdown_dollars, max_drawdown, v.maximum_drawdown_dollars),
            _gate("doubled_cost_pnl", doubled_cost_pnl >= v.minimum_doubled_cost_pnl, doubled_cost_pnl, v.minimum_doubled_cost_pnl),
            _gate("broker_fill_fraction", broker["fill_fraction"] >= v.minimum_broker_fill_fraction, broker["fill_fraction"], v.minimum_broker_fill_fraction),
            _gate("mean_fill_slippage", broker["mean_fill_slippage_dollars"] <= v.maximum_mean_fill_slippage_dollars, broker["mean_fill_slippage_dollars"], v.maximum_mean_fill_slippage_dollars),
            _gate("reconciliation_blocks", reconciliation_blocks <= v.maximum_reconciliation_blocks, reconciliation_blocks, v.maximum_reconciliation_blocks),
            _gate("modeled_max_loss_holds", not loss_ratio_violations, loss_ratio_violations, f"no loss > {v.maximum_realized_loss_to_model_ratio:.2f}x modeled max"),
            _gate("tested_regimes", len(tested_regimes) >= v.minimum_tested_regimes, len(tested_regimes), v.minimum_tested_regimes),
            _gate("regime_coverage", regime_coverage >= v.minimum_regime_coverage_fraction, regime_coverage, v.minimum_regime_coverage_fraction),
            _gate("regime_expectancy", (not v.require_nonnegative_regime_expectancy) or not negative_tested_regimes, negative_tested_regimes, "no negative expectancy tested regime"),
            _gate("deterministic_replay", (not v.require_replay_consistency) or bool(replay and replay.get("status") == "PASSED"), replay.get("status") if replay else None, "PASSED"),
        ]

        failed = [gate for gate in gates if not gate["passed"]]
        status = "ELIGIBLE_FOR_MANUAL_LIVE_REVIEW" if not failed else "PAPER_VALIDATION_INCOMPLETE"
        created_at = utc_iso(utc_now())
        report = {
            "schema_version": 1,
            "validation_id": f"V-{hashlib.sha256((created_at + self.config.production_fingerprint()).encode()).hexdigest()[:16]}",
            "created_at": created_at,
            "status": status,
            "approved": False,
            "automatic_live_enable": False,
            "model_version": self.config.prediction.model_version,
            "config_fingerprint": self.config.production_fingerprint(),
            "evidence_scope": "production_realtime_market_data_plus_tradier_sandbox_execution",
            "sessions": len(sessions),
            "matured_forecasts": len(forecasts),
            "trades": len(trades),
            "metrics": {
                "verified_data_fraction": verified_fraction,
                "production_snapshot_fraction": production_source_fraction,
                "required_input_fraction": required_input_fraction,
                "pq_ready_fraction": pq_ready_fraction,
                "horizons": horizon_metrics,
                "net_pnl": net_pnl,
                "profit_factor": profit_factor,
                "expectancy_lcb": expectancy_lcb,
                "recent_trade_window": len(recent_pnls),
                "recent_net_pnl": recent_net_pnl,
                "recent_profit_factor": recent_profit_factor,
                "max_drawdown": max_drawdown,
                "worst_trade": worst_trade,
                "doubled_cost_pnl": doubled_cost_pnl,
                "broker": broker,
                "reconciliation_blocks": reconciliation_blocks,
                "regime_coverage": regime_coverage,
                "tested_regimes": regime_report,
                "loss_ratio_violations": loss_ratio_violations,
                "replay": replay,
            },
            "gates": gates,
            "failed_gates": [gate["name"] for gate in failed],
            "statement": (
                "Paper-validation gates passed. Candidate is eligible for separate manual live review; "
                "real-money trading remains locked."
                if not failed
                else "Paper-validation is incomplete. Real-money trading remains locked."
            ),
        }
        row = {
            "validation_id": report["validation_id"],
            "created_at": created_at,
            "status": status,
            "model_version": self.config.prediction.model_version,
            "config_hash": self.config.production_fingerprint(),
            "sessions": len(sessions),
            "matured_forecasts": len(forecasts),
            "trades": len(trades),
            "payload": report,
        }
        self.journal.insert_validation_run(row)
        path: Path = self.config.paths.promotion_report
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        tmp.replace(path)
        return report

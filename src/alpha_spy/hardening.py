from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .broker_reconcile import BrokerReconciler, expected_position_map
from .config import SuiteConfig
from .context import MarketContext, build_market_context
from .events import EventState, event_state_at
from .execution import ExecutionManager
from .features import compute_features
from .fail_safes import as_dict, build_position_signal, impulse_allows_off_grid, must_fail_safe_flatten
from .position_management import evaluate_position
from .prediction import create_prediction_bundle
from .regime import estimate_dealer_gamma_proxy, estimate_option_activity_proxy
from .risk import AccountState, choose_decision, no_trade_decision, parse_account_state
from .services import EngineService, SettlementService, StopFlag, append_jsonl, sleep_interruptible
from .session_tape import resolve_session_open_spy
from .strategy import generate_candidates
from .timeutil import ET, at_or_after_et, et_now, utc_iso, utc_now
from .tradier import TradierClient, TradierError, preview_fees


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed


def _state_from_trust(config: SuiteConfig, trust: float) -> str:
    if trust >= config.audit.health_green:
        return "GREEN"
    if trust >= config.audit.health_yellow:
        return "YELLOW"
    if trust >= config.audit.health_orange:
        return "ORANGE"
    return "RED"


def _input_health(
    config: SuiteConfig,
    snapshot: dict[str, Any],
    chain: dict[str, Any] | None,
    options: list[dict[str, Any]],
    surface: dict[str, Any] | None,
    gamma: dict[str, Any],
    *,
    context: MarketContext,
    event: EventState,
) -> dict[str, Any]:
    required_failures: list[str] = []
    confidence_issues: list[str] = []
    advisory: list[str] = []

    if snapshot.get("integrity") != "VERIFIED":
        required_failures.append("market_snapshot_not_verified")
    if float(snapshot.get("covered_weight") or 0.0) < config.universe.minimum_covered_weight:
        required_failures.append("constituent_coverage_below_minimum")

    if config.risk.require_verified_option_chain:
        if not chain or not options:
            required_failures.append("spy_option_chain_missing")
        elif chain.get("integrity") != "VERIFIED":
            required_failures.append("spy_option_chain_not_verified")
        else:
            try:
                chain_time = _parse_iso(chain["captured_at"])
                snapshot_time = _parse_iso(snapshot["captured_at"])
                age = abs((chain_time - snapshot_time).total_seconds())
                if age > config.market.option_quote_stale_seconds:
                    required_failures.append("spy_option_chain_stale")
            except Exception:
                required_failures.append("spy_option_chain_timestamp_invalid")

    if config.risk.require_verified_surface:
        if not surface:
            required_failures.append("surface_metrics_missing")
        else:
            if surface.get("integrity") != "VERIFIED":
                required_failures.append("surface_metrics_not_verified")
            if float(surface.get("covered_weight") or 0.0) < config.risk.minimum_surface_coverage:
                required_failures.append("surface_coverage_below_minimum")

    trust_multiplier = 1.0
    if gamma.get("state") == "unknown_gamma":
        confidence_issues.append("dealer_gamma_proxy_unavailable")
        trust_multiplier *= 0.95
    if event.source not in {"calendar", "disabled"}:
        if config.context.event_calendar_enabled and config.context.event_calendar_required:
            required_failures.append(f"event_calendar:{event.source}")
        else:
            confidence_issues.append("event_calendar_unavailable")
            trust_multiplier *= 0.94
    if event.blocked:
        required_failures.append(f"event_entry_block:{event.state}")
    if not context.required_ok:
        required_failures.extend(f"context:{name}" for name in context.required_failures)
    if context.coverage < config.context.minimum_context_coverage:
        confidence_issues.append("context_coverage_below_minimum")
        trust_multiplier *= max(0.75, context.coverage / max(config.context.minimum_context_coverage, 1e-9))
    if context.confidence_issues:
        trust_multiplier *= max(0.85, 1.0 - 0.01 * len(context.confidence_issues))
    if surface and surface.get("skew_gap") is None:
        confidence_issues.append("skew_context_missing")
        trust_multiplier *= 0.97

    advisory.extend(["QQQ", "IWM", "HYG", "UUP", "SHY", "IEF", "TLT", "sector ETFs", "VIX family"])
    return {
        "required_ok": not required_failures,
        "required_failures": required_failures,
        "confidence_issues": confidence_issues,
        "trust_multiplier": trust_multiplier,
        "advisory_context": advisory,
        "classification": {
            "required": ["SPY/constituent snapshot", "SPY executable option chain", "SPY/constituent IV surface"],
            "confidence_reducing": ["dealer gamma proxy", "event calendar", "skew context", "cross-asset proxy coverage"],
            "advisory": advisory,
        },
    }


def _on_entry_grid(config: SuiteConfig, captured_at: str) -> bool:
    local = _parse_iso(captured_at).astimezone(ET)
    grid = max(1, int(config.risk.entry_grid_minutes))
    return local.minute % grid == 0


def _leg_contracts(position: dict[str, Any]) -> int:
    return sum(max(1, int(leg.get("quantity", 1))) for leg in position.get("legs", []))


class HardenedEngineService(EngineService):
    """Production decision path: explicit input health, regime state, cadence and reconciliation."""

    def run_once(self) -> str | None:
        self._process_commands()
        snapshot = self.journal.latest_snapshot()
        if not snapshot:
            return None
        last = self.journal.get_control("last_engine_snapshot_id")
        if last == snapshot["snapshot_id"]:
            return None
        ready = self.journal.get_control("market_ready_snapshot_id")
        # Streaming market snapshots are inserted before their option/surface context.
        # Never consume a partially materialized observation. Demo/legacy collectors
        # don't set this marker, so they retain the historical behavior.
        if snapshot.get("source") == "tradier_production_stream" and ready != snapshot["snapshot_id"]:
            return None

        chain, options = self.journal.latest_option_chain("SPY")
        if chain:
            try:
                chain_age = abs(
                    (_parse_iso(snapshot["captured_at"]) - _parse_iso(chain["captured_at"])).total_seconds()
                )
            except Exception:
                chain_age = float("inf")
            if chain_age > self.config.market.option_quote_stale_seconds:
                options = []
        quotes = self.journal.snapshot_quotes(snapshot["snapshot_id"])
        feature = compute_features(self.journal, self.config, snapshot, quotes)
        surface = self.journal.surface_metrics(snapshot["snapshot_id"])
        spot = float(snapshot.get("spy_price") or 0.0)
        gamma = estimate_dealer_gamma_proxy(options, spot)
        option_activity = estimate_option_activity_proxy(options, spot)
        context = build_market_context(self.journal, self.config, snapshot, quotes)
        event = event_state_at(self.config, _parse_iso(snapshot["captured_at"]))
        # Operator override exists only as an emergency *more restrictive* state.
        # It may block macro/rebalance trading, but cannot force an event window to ordinary.
        configured_event = self.journal.get_control("event_state", "").strip()
        if configured_event in {"macro_announcement", "rebalance", "earnings_heavy", "unknown"}:
            event = EventState(
                state=configured_event,
                source="operator_override",
                title="operator override",
                blocked=configured_event in {"macro_announcement", "rebalance", "unknown"},
            )
        input_health = _input_health(
            self.config,
            snapshot,
            chain,
            options,
            surface,
            gamma,
            context=context,
            event=event,
        )
        feature.setdefault("payload", {})["hardening"] = {
            "gamma": gamma,
            "option_activity": option_activity,
            "event_state": event.state,
            "event": event.as_dict(),
            "market_context": context.as_dict(),
            "input_health": input_health,
        }
        trust = float(feature.get("trust_score") or 0.0) * float(input_health["trust_multiplier"])
        if not input_health["required_ok"]:
            trust = 0.0
            feature["health_state"] = "RED"
        else:
            feature["health_state"] = _state_from_trust(self.config, trust)
        feature["trust_score"] = max(0.0, min(1.0, trust))
        self.journal.insert_features(feature)

        predictions = create_prediction_bundle(
            self.journal, self.config, snapshot, feature, quotes=quotes
        )
        for member in predictions:
            self.journal.insert_prediction(member)
        prediction = next(
            (member for member in predictions if int(member["horizon_minutes"]) == int(self.config.prediction.horizon_minutes)),
            predictions[0],
        )
        candidates = generate_candidates(self.config, prediction, options)
        self.journal.insert_candidates(candidates)
        account = self._account_state()

        reconciliation = BrokerReconciler(self.config, self.journal).check(self.journal.open_position())
        distribution = prediction.get("payload", {}).get("distribution", {})
        pq_surface_weight = float(distribution.get("q_surface_covered_weight") or 0.0)
        model_uncertainty = float(prediction.get("payload", {}).get("model_uncertainty") or 0.0)
        if not input_health["required_ok"]:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "input_integrity_block",
                payload={"input_health": input_health},
            )
        elif pq_surface_weight < self.config.prediction.minimum_pq_surface_weight:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "pq_surface_coverage_block",
                payload={"q_surface_covered_weight": pq_surface_weight},
            )
        elif model_uncertainty >= self.config.prediction.max_model_uncertainty:
            decision = no_trade_decision(
                prediction, feature, account, "model_uncertainty_block",
                payload={"model_uncertainty": model_uncertainty},
            )
        elif self.config.risk.block_on_broker_reconciliation and reconciliation.blocked:
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "broker_reconciliation_blocked",
                payload={"reconciliation": reconciliation.as_dict()},
            )
        elif not _on_entry_grid(self.config, snapshot["captured_at"]) and not self._impulse_override(
            snapshot
        ):
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                "off_entry_grid",
                payload={"entry_grid_minutes": self.config.risk.entry_grid_minutes},
            )
        else:
            impulse = self._impulse_override(snapshot)
            local = _parse_iso(snapshot["captured_at"]).astimezone(ET)
            grid = max(1, int(self.config.risk.entry_grid_minutes))
            bucket_minute = local.minute - (local.minute % grid)
            bucket = local.replace(minute=bucket_minute, second=0, microsecond=0).isoformat()
            if not impulse and self.journal.get_control("last_entry_grid_bucket") == bucket:
                decision = no_trade_decision(
                    prediction,
                    feature,
                    account,
                    "entry_grid_already_evaluated",
                    payload={"entry_grid_bucket": bucket},
                )
            else:
                if not impulse:
                    self.journal.set_control("last_entry_grid_bucket", bucket)
                decision = choose_decision(
                    self.config,
                    self.journal,
                    prediction,
                    feature,
                    candidates,
                    account,
                    now=_parse_iso(snapshot["captured_at"]),
                )
                if impulse and decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"}:
                    self.journal.set_control(
                        "last_impulse_entry_spot", str(float(snapshot.get("spy_price") or 0.0))
                    )
        self.journal.insert_decision(decision)

        if decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"} and decision.get("candidate_id"):
            candidate = next(
                (c for c in candidates if c["candidate_id"] == decision["candidate_id"]), None
            )
            if candidate:
                try:
                    self.execution.execute(decision, candidate)
                except Exception as exc:
                    self.journal.alert("critical", "Order execution failed", str(exc), "execution")
                    self.publisher.alert("critical", "Order execution failed", str(exc), "execution")

        self.journal.set_control("last_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, prediction, account)
        append_jsonl(
            self.config.paths.state_root
            / "candidates"
            / f"candidates-{et_now().date().isoformat()}.jsonl",
            {
                "prediction": prediction,
                "prediction_bundle": predictions,
                "feature": feature,
                "candidates": candidates,
                "decision": decision,
                "reconciliation": reconciliation.as_dict(),
            },
        )
        return f"prediction={prediction['prediction_id']} action={decision['action']}"

    def _impulse_override(self, snapshot: dict[str, Any]) -> bool:
        high, low = self.journal.session_spy_extrema(str(snapshot.get("captured_at") or ""))
        last = self.journal.get_control("last_impulse_entry_spot")
        last_spot = float(last) if last else None
        return impulse_allows_off_grid(
            session_high=high,
            session_low=low,
            spot=float(snapshot.get("spy_price") or 0.0),
            last_impulse_spot=last_spot,
        )

    def _account_state(self) -> AccountState:
        session_date = et_now().date().isoformat()
        managed_pnl = self.journal.managed_daily_pnl(session_date)
        broker_submission = bool(
            self.config.trading.enabled and self.config.trading.submit_orders
        )
        broker_configured = bool(
            self.config.tradier.access_token.get_secret_value() and self.config.tradier.account_id
        )
        if broker_configured:
            try:
                with TradierClient(self.config) as client:
                    broker = parse_account_state(client.balances())
                if broker.valid:
                    return AccountState(
                        equity=broker.equity,
                        cash=broker.cash,
                        buying_power=broker.buying_power,
                        daily_pnl=managed_pnl,
                        valid=True,
                        source="broker",
                    )
                if broker_submission:
                    return AccountState(
                        broker.equity,
                        broker.cash,
                        broker.buying_power,
                        managed_pnl,
                        valid=False,
                        source="broker_invalid",
                        reason=broker.reason,
                    )
            except Exception as exc:
                severity = "critical" if broker_submission else "warning"
                self.journal.alert(severity, "Account poll failed", str(exc), "engine")
                if broker_submission:
                    return AccountState(
                        0.0,
                        0.0,
                        0.0,
                        managed_pnl,
                        valid=False,
                        source="broker_error",
                        reason=str(exc),
                    )
        return AccountState(
            25_000.0,
            25_000.0,
            25_000.0,
            managed_pnl,
            valid=True,
            source="local_paper",
        )


@dataclass(frozen=True)
class ExitFill:
    status: str
    exit_value: float
    fees: float
    broker_order_ids: list[str]
    payload: dict[str, Any]


class HardenedSettlementService(SettlementService):
    """One-minute professional position manager with broker-authoritative flattening."""

    def run_forever(self) -> None:
        flag = StopFlag()
        while not flag.stopped:
            started = time.perf_counter()
            try:
                status = self.run_once()
                latency = (time.perf_counter() - started) * 1000.0
                self.journal.heartbeat("settlement", "ONLINE", latency, status)
            except Exception as exc:
                self.journal.heartbeat("settlement", "ERROR", None, str(exc))
                self.journal.alert("critical", "Settlement cycle failed", str(exc), "settlement")
                try:
                    self._fail_safe_recover(exc)
                except Exception as inner:
                    self.journal.alert(
                        "critical",
                        "Settlement watchdog flatten failed",
                        str(inner),
                        "settlement",
                    )
            sleep_interruptible(flag, self.config.risk.exit_monitor_interval_seconds)

    def _held_leg_quotes(self, position: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        _, options = self.journal.latest_option_chain("SPY")
        quotes = {str(row["symbol"]): row for row in options}
        missing = [
            str(leg["symbol"])
            for leg in position.get("legs", [])
            if str(leg["symbol"]) not in quotes
        ]
        if missing and self.config.tradier.market_access_token.get_secret_value():
            try:
                with TradierClient(self.config, purpose="market") as client:
                    direct = client.quotes(missing, greeks=False)
                for raw in direct:
                    symbol = str(raw.get("symbol") or "")
                    if not symbol:
                        continue
                    quotes[symbol] = {
                        "symbol": symbol,
                        "bid": float(raw.get("bid") or 0.0),
                        "ask": float(raw.get("ask") or 0.0),
                        "midpoint": (
                            (float(raw.get("bid") or 0.0) + float(raw.get("ask") or 0.0)) / 2.0
                            if float(raw.get("bid") or 0.0) > 0 and float(raw.get("ask") or 0.0) > 0
                            else float(raw.get("last") or 0.0)
                        ),
                    }
            except Exception as exc:
                self.journal.alert("warning", "Held-leg direct quote failed", str(exc), "settlement")
        missing = [
            str(leg["symbol"])
            for leg in position.get("legs", [])
            if str(leg["symbol"]) not in quotes
        ]
        return quotes, missing

    @staticmethod
    def _marked_value(position: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> float:
        current_net = 0.0
        for leg in position.get("legs", []):
            quote = quotes[str(leg["symbol"])]
            quantity = int(leg.get("quantity", 1))
            if str(leg["side"]).startswith("buy"):
                liquidation = float(quote.get("bid") or quote.get("midpoint") or 0.0)
                current_net += liquidation * quantity
            else:
                cover = float(quote.get("ask") or quote.get("midpoint") or 0.0)
                current_net -= cover * quantity
        return abs(current_net)

    def _marked_pnl(self, position: dict[str, Any], current_value: float) -> float:
        entry_kind = position.get("payload", {}).get("entry_kind", "debit")
        quantity = int(position["quantity"])
        entry_fees = float(position.get("payload", {}).get("entry_fees") or 0.0)
        estimated_exit_fees = _leg_contracts(position) * self.config.trading.fee_per_contract * quantity
        if entry_kind == "debit":
            premium_pnl = (current_value - float(position["entry_value"])) * 100.0 * quantity
        else:
            premium_pnl = (float(position["entry_value"]) - current_value) * 100.0 * quantity
        return premium_pnl - entry_fees - estimated_exit_fees

    def run_once(self) -> str:
        try:
            return self._manage_open_position()
        except Exception as exc:
            return self._fail_safe_recover(exc)

    def _fail_safe_recover(self, exc: Exception) -> str:
        position = self.journal.open_position()
        if not position:
            return f"fail_safe_already_flat:{exc}"
        payload = position.setdefault("payload", {})
        errors = int(payload.get("management_error_count") or 0) + 1
        payload["management_error_count"] = errors
        payload["last_management_error"] = str(exc)[:2000]
        payload["unmanageable"] = errors >= 3
        now = utc_now()
        opened_at = None
        try:
            opened_at = _parse_iso(str(position.get("opened_at") or ""))
        except Exception:
            opened_at = None
        flatten = must_fail_safe_flatten(
            now=now,
            forced_flat_time_et=self.config.risk.forced_flat_time_et,
            error_count=errors,
            flatten_requested=self.journal.get_control("flatten_requested", "false").lower()
            == "true",
            opened_at=opened_at,
        )
        self.journal.upsert_position(position)
        self.journal.alert(
            "critical",
            "Settlement fail-safe engaged",
            str(exc),
            "settlement",
            {"errors": errors, "flatten": flatten, "position_id": position.get("position_id")},
        )
        if not flatten:
            return f"management_error={errors}"
        return self._local_force_close(position, "fail_safe_flatten")

    def _local_force_close(self, position: dict[str, Any], reason: str) -> str:
        now = utc_now()
        try:
            if BrokerReconciler(self.config, self.journal).broker_mode:
                self.config.assert_broker_submission_safe()
                self._close_live_authoritative(position, current_value=None, force_market=True)
        except Exception as exc:
            self.journal.alert(
                "critical",
                "Fail-safe broker flatten failed; closing local book",
                str(exc),
                "settlement",
            )
        clock_flat = at_or_after_et(now, self.config.risk.forced_flat_time_et) or at_or_after_et(
            now, "16:00"
        )
        if self.config.trading.paper_mode or clock_flat:
            position.update(
                {
                    "status": "CLOSED",
                    "closed_at": utc_iso(),
                    "exit_reason": reason,
                    "unrealized_pnl": 0.0,
                    "realized_pnl": float(position.get("unrealized_pnl") or 0.0),
                }
            )
            self.journal.set_control("flatten_requested", "false")
            self.journal.upsert_position(position)
        return reason

    def _manage_open_position(self) -> str:
        position = self.journal.open_position()
        if not position:
            reconciliation = BrokerReconciler(self.config, self.journal).check(None)
            return "broker_reconciliation_blocked" if reconciliation.blocked else "no_position"

        now = utc_now()
        operator_flatten = self.journal.get_control("flatten_requested", "false").lower() == "true"
        forced_flat = at_or_after_et(now, self.config.risk.forced_flat_time_et)
        if operator_flatten:
            self.journal.set_control("flatten_requested", "false")

        reconciliation = BrokerReconciler(self.config, self.journal).check(position)
        broker_mode = BrokerReconciler(self.config, self.journal).broker_mode
        quotes, missing = self._held_leg_quotes(position)

        if missing and not (operator_flatten or forced_flat):
            position.setdefault("payload", {})["broker_reconciliation"] = reconciliation.reason
            self.journal.upsert_position(position)
            return f"missing_marks={len(missing)}"

        if missing:
            current_value = float(position.get("current_value") or position["entry_value"])
            pnl = float(position.get("unrealized_pnl") or 0.0)
        else:
            current_value = self._marked_value(position, quotes)
            pnl = self._marked_pnl(position, current_value)

        mfe = max(float(position.get("mfe") or 0.0), pnl)
        mae = min(float(position.get("mae") or 0.0), pnl)
        snapshot = as_dict(self.journal.latest_snapshot())
        feature = as_dict(self.journal.latest_features())
        prediction = as_dict(
            self.journal.latest_prediction_for_horizon("15m") or self.journal.latest_prediction()
        )
        surface = as_dict(as_dict(feature.get("payload")).get("surface"))
        spy_iv = surface.get("spy_iv")
        constituent_iv = surface.get("constituent_iv")
        iv_edge_gap = (
            float(constituent_iv) - float(spy_iv)
            if spy_iv is not None and constituent_iv is not None
            else 0.0
        )
        context_signals = (prediction.get("payload") or {}).get("market_context", {}).get("signals", {})
        if not isinstance(context_signals, dict):
            context_signals = {}
        session_open = float(context_signals.get("session_open_price") or 0.0)
        if session_open <= 0:
            session_open = float(resolve_session_open_spy(self.journal, snapshot) or 0.0)
        signal = build_position_signal(
            forecast_return=float(prediction.get("expected_return") or 0.0),
            breadth=float(feature.get("breadth") or 0.5),
            iv_edge_gap=iv_edge_gap,
            spot=float(snapshot.get("spy_price") or 0.0),
            session_open=session_open,
            vwap_distance_bps=float(context_signals.get("auction_vwap_distance_bps") or 0.0),
            situation_tradeable=bool(context_signals.get("situation_tradeable")),
        )
        management = evaluate_position(position, now=now, pnl=pnl, mfe=mfe, signal=signal)

        close_reason = (
            "operator_flatten"
            if operator_flatten
            else "forced_flat_time"
            if forced_flat
            else management.reason
            if management.should_exit
            else None
        )
        payload = position.setdefault("payload", {})
        payload["management_state"] = management.state
        payload["management_decision"] = management.as_dict()
        payload["broker_reconciliation"] = reconciliation.reason
        position.update(
            {
                "current_value": current_value,
                "unrealized_pnl": pnl,
                "mfe": mfe,
                "mae": mae,
            }
        )

        if close_reason:
            exit_fill: ExitFill | None = None
            if broker_mode:
                self.config.assert_broker_submission_safe()
                exit_fill = self._close_live_authoritative(
                    position,
                    current_value=None if missing else current_value,
                    force_market=forced_flat or bool(missing) or reconciliation.blocked,
                )
                entry_kind = payload.get("entry_kind", "debit")
                entry_fees = float(payload.get("entry_fees") or 0.0)
                if entry_kind == "debit":
                    realized = (
                        (exit_fill.exit_value - float(position["entry_value"]))
                        * 100.0
                        * int(position["quantity"])
                        - entry_fees
                        - exit_fill.fees
                    )
                else:
                    realized = (
                        (float(position["entry_value"]) - exit_fill.exit_value)
                        * 100.0
                        * int(position["quantity"])
                        - entry_fees
                        - exit_fill.fees
                    )
                payload["exit_fill"] = {
                    "status": exit_fill.status,
                    "exit_value": exit_fill.exit_value,
                    "fees": exit_fill.fees,
                    "broker_order_ids": exit_fill.broker_order_ids,
                    "payload": exit_fill.payload,
                }
                # Verify broker flat after the exit before local closure.
                post = BrokerReconciler(self.config, self.journal).check(None)
                if post.blocked:
                    raise TradierError(
                        f"Broker still shows managed exposure after exit: {post.reason}"
                    )
            else:
                realized = pnl

            position.update(
                {
                    "status": "CLOSED",
                    "closed_at": utc_iso(),
                    "realized_pnl": realized,
                    "unrealized_pnl": 0.0,
                    "exit_reason": close_reason,
                }
            )
        self.journal.upsert_position(position)
        return close_reason or f"pnl={pnl:.2f}"

    def _close_live_authoritative(
        self,
        position: dict[str, Any],
        *,
        current_value: float | None,
        force_market: bool,
    ) -> ExitFill:
        expected = expected_position_map(position)
        with TradierClient(self.config) as client:
            broker_positions = {
                str(row.get("symbol") or row.get("option_symbol") or ""): float(row.get("quantity") or 0.0)
                for row in client.positions()
                if row.get("symbol") or row.get("option_symbol")
            }
            actual = {symbol: broker_positions.get(symbol, 0.0) for symbol in expected}
            if not any(abs(qty) > 1e-9 for qty in actual.values()):
                self.journal.alert(
                    "critical",
                    "Local position was already flat at broker",
                    position["position_id"],
                    "settlement",
                    {"expected": expected, "actual": actual},
                )
                return ExitFill(
                    status="ALREADY_FLAT",
                    exit_value=float(current_value or position.get("current_value") or position["entry_value"]),
                    fees=0.0,
                    broker_order_ids=[],
                    payload={"expected": expected, "actual": actual},
                )

            exact = all(abs(actual.get(symbol, 0.0) - qty) <= 1e-9 for symbol, qty in expected.items())
            if exact:
                return self._close_as_structure(
                    client,
                    position,
                    current_value=current_value,
                    force_market=force_market,
                )
            return self._close_mismatched_legs(client, position, actual)

    def _wait_for_fill(
        self, client: TradierClient, broker_id: str | int, seconds: int
    ) -> tuple[dict[str, Any], str]:
        deadline = time.monotonic() + max(3, int(seconds))
        state: dict[str, Any] = {}
        status = "UNKNOWN"
        while time.monotonic() < deadline:
            time.sleep(1)
            state = client.order(broker_id)
            status = ExecutionManager._status(state)
            if status in ExecutionManager.TERMINAL:
                break
        return state, status

    def _place_exit(
        self, client: TradierClient, payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        preview = client.preview_order(payload)
        if not ExecutionManager._preview_ok(preview):
            raise TradierError(f"Exit preview failed: {preview}")
        response = client.place_order(payload)
        broker_id = response.get("id")
        if not broker_id:
            raise TradierError(f"Exit order did not return an id: {response}")
        return str(broker_id), response, preview

    def _close_as_structure(
        self,
        client: TradierClient,
        position: dict[str, Any],
        *,
        current_value: float | None,
        force_market: bool,
    ) -> ExitFill:
        reverse = {
            "buy_to_open": "sell_to_close",
            "sell_to_open": "buy_to_close",
            "buy_to_close": "sell_to_open",
            "sell_to_close": "buy_to_open",
        }
        legs = [{**leg, "side": reverse[leg["side"]]} for leg in position["legs"]]
        closing_kind = "credit" if position.get("payload", {}).get("entry_kind", "debit") == "debit" else "debit"
        use_market = bool(force_market or current_value is None)
        payload: dict[str, Any] = {
            "class": "option" if len(legs) == 1 else "multileg",
            "symbol": "SPY",
            "duration": "day",
            "type": "market" if use_market else ("limit" if len(legs) == 1 else closing_kind),
            "tag": f"{self.config.trading.tag_prefix}-EXIT-{position['position_id'][-6:]}",
        }
        if not use_market:
            payload["price"] = f"{float(current_value):.2f}"
        if len(legs) == 1:
            payload.update(
                {
                    "option_symbol": legs[0]["symbol"],
                    "side": legs[0]["side"],
                    "quantity": int(position["quantity"]) * int(legs[0].get("quantity", 1)),
                }
            )
        else:
            for index, leg in enumerate(legs):
                payload[f"option_symbol[{index}]"] = leg["symbol"]
                payload[f"side[{index}]"] = leg["side"]
                payload[f"quantity[{index}]"] = int(position["quantity"]) * int(leg.get("quantity", 1))

        broker_id, response, preview = self._place_exit(client, payload)
        state, status = self._wait_for_fill(
            client, broker_id, max(5, self.config.trading.limit_price_wait_seconds * 2)
        )
        broker_ids = [broker_id]
        previews = [preview]
        if status != "FILLED":
            if status not in ExecutionManager.TERMINAL:
                client.cancel_order(broker_id)
                state, status = self._wait_for_fill(
                    client, broker_id, self.config.trading.cancel_confirm_seconds
                )
            if (
                status != "FILLED"
                and not use_market
                and self.config.trading.force_market_exit_after_limit_failure
            ):
                market_payload = dict(payload)
                market_payload["type"] = "market"
                market_payload.pop("price", None)
                broker_id2, response2, preview2 = self._place_exit(client, market_payload)
                broker_ids.append(broker_id2)
                previews.append(preview2)
                state, status = self._wait_for_fill(
                    client, broker_id2, max(5, self.config.trading.limit_price_wait_seconds * 2)
                )
                response = response2
            if status != "FILLED":
                raise TradierError(f"Exit order not filled; terminal status={status}")

        fill = ExecutionManager._average_fill(state)
        if fill is None:
            raise TradierError(f"Exit filled but average fill price is missing: {state}")
        fees = sum(float(preview_fees(item) or 0.0) for item in previews)
        return ExitFill(
            status=status,
            exit_value=abs(float(fill)),
            fees=fees,
            broker_order_ids=broker_ids,
            payload={"request": payload, "response": response, "order": state, "previews": previews},
        )

    def _close_mismatched_legs(
        self,
        client: TradierClient,
        position: dict[str, Any],
        actual: dict[str, float],
    ) -> ExitFill:
        """Emergency reconciliation flatten: close actual shorts first, then longs."""
        ordered = sorted(
            [(symbol, qty) for symbol, qty in actual.items() if abs(qty) > 1e-9],
            key=lambda item: 0 if item[1] < 0 else 1,
        )
        net_cashflow = 0.0
        fees = 0.0
        broker_ids: list[str] = []
        orders: list[dict[str, Any]] = []
        for symbol, qty in ordered:
            side = "buy_to_close" if qty < 0 else "sell_to_close"
            payload = {
                "class": "option",
                "symbol": "SPY",
                "option_symbol": symbol,
                "side": side,
                "quantity": round(abs(qty)),
                "duration": "day",
                "type": "market",
                "tag": f"{self.config.trading.tag_prefix}-RECON-{position['position_id'][-6:]}",
            }
            broker_id, response, preview = self._place_exit(client, payload)
            state, status = self._wait_for_fill(
                client, broker_id, max(5, self.config.trading.limit_price_wait_seconds * 2)
            )
            if status != "FILLED":
                raise TradierError(f"Emergency leg flatten {broker_id} not filled: {status}")
            fill = ExecutionManager._average_fill(state)
            if fill is None:
                raise TradierError(f"Emergency leg flatten missing fill price: {state}")
            signed_cash = -float(fill) * abs(qty) if qty < 0 else float(fill) * abs(qty)
            net_cashflow += signed_cash
            fees += float(preview_fees(preview) or 0.0)
            broker_ids.append(broker_id)
            orders.append({"payload": payload, "response": response, "state": state, "preview": preview})
        units = max(1, int(position.get("quantity") or 1))
        return ExitFill(
            status="FILLED_RECONCILED",
            exit_value=abs(net_cashflow / units),
            fees=fees,
            broker_order_ids=broker_ids,
            payload={"emergency_reconciliation": True, "orders": orders, "actual": actual},
        )

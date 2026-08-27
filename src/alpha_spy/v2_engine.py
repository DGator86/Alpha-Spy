from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from . import strategy_v2_complete as _complete_geometry  # noqa: F401
from .execution import build_multileg_payload
from .features import compute_features
from .prediction import create_prediction
from .risk import choose_decision, no_trade_decision
from .strategy_v2_prior import generate_v2_candidates as generate_shadow_candidates
from .tradier import TradierClient, preview_fees
from .v2_hgb_vertical import build_hgb_vertical_candidate
from .v2_services import V2EngineService as _BaseV2EngineService


DEFAULT_BETA_V2_STATE_URL = "http://127.0.0.1:8790/api/state"


class V2EngineService(_BaseV2EngineService):
    """Authoritative Alpha V2 engine for the validated HGB + 2-point vertical path.

    The 47-family P/Q optimizer is retained as shadow research telemetry. It cannot
    become an executable candidate until a later calibration study explicitly
    promotes it. This prevents the old synthetic-EV Christmas-tree niche from
    overriding the causal directional signal that survived blocked walk-forward,
    parameter perturbation, feature ablation, and execution stress.
    """

    def __init__(self, config, journal, *, beta_state_url: str | None = None, **kwargs: Any):
        super().__init__(
            config,
            journal,
            beta_state_url=beta_state_url or DEFAULT_BETA_V2_STATE_URL,
            **kwargs,
        )

    def _preview_fee_gate(self, candidate: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        has_execution_preview = bool(
            self.config.tradier.access_token.get_secret_value()
            and self.config.tradier.account_id
        )
        if not has_execution_preview:
            return True, {
                "preview": "execution_credentials_unavailable",
                "cost_source": "actual_chain_quote_drag_plus_pass_through_estimate",
            }

        try:
            payload = build_multileg_payload(candidate, 1, float(candidate["entry_price"]))
            with TradierClient(self.config) as client:
                preview = client.preview_order(payload)
            one_way = preview_fees(preview)
            if one_way is None:
                return True, {
                    "preview": preview,
                    "cost_source": "actual_chain_quote_drag_plus_pass_through_estimate",
                    "preview_fee_fields_missing": True,
                }
            roundtrip = 2.0 * one_way
        except Exception as exc:
            if self.config.trading.submit_orders:
                return False, {
                    "preview_error": str(exc),
                    "cost_source": "broker_preview_failed_closed",
                }
            return True, {
                "preview_error": str(exc),
                "cost_source": "actual_chain_quote_drag_plus_pass_through_estimate",
            }

        authority = str((candidate.get("payload") or {}).get("authority") or "")
        if authority == "beta_v2_hgb_blocked_walk_forward":
            estimated_fees = float(
                ((candidate.get("payload") or {}).get("execution") or {}).get(
                    "estimated_roundtrip_fees_dollars"
                )
                or 0.0
            )
            risk_ex_fees = max(0.0, float(candidate.get("max_loss") or 0.0) - estimated_fees)
            preview_risk = risk_ex_fees + roundtrip
            # Preview is a cost/risk veto only. Do not resurrect the uncalibrated
            # legacy forecast-EV gate by comparing fees with candidate.expected_value.
            ok = roundtrip <= 3.0 and preview_risk <= 100.0 + 1e-9
            return ok, {
                "preview": preview,
                "preview_roundtrip_fee_estimate": roundtrip,
                "preview_max_loss_dollars": preview_risk,
                "cost_source": "tradier_order_preview",
                "preview_policy": "hgb_execution_cost_and_risk_veto_only",
            }

        net_after_preview = float(candidate.get("expected_value") or 0.0) - roundtrip
        return net_after_preview > 0.0, {
            "preview_roundtrip_fee_estimate": roundtrip,
            "expected_value_after_preview_fees": net_after_preview,
            "cost_source": "tradier_order_preview",
        }

    @staticmethod
    def _mark_shadow(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for candidate in candidates:
            candidate["status"] = "SHADOW"
            candidate["rejection_reason"] = "v2_shadow_optimizer_not_authorized"
            payload = dict(candidate.get("payload") or {})
            payload["shadow_only"] = True
            payload["execution_authority"] = False
            candidate["payload"] = payload
        return candidates

    def run_once(self) -> str | None:
        self._process_commands()
        snapshot = self.journal.latest_snapshot()
        if not snapshot:
            return None
        last = self.journal.get_control("last_v2_engine_snapshot_id")
        if last == snapshot["snapshot_id"]:
            return None

        quotes = self.journal.snapshot_quotes(snapshot["snapshot_id"])
        feature = compute_features(self.journal, self.config, snapshot, quotes)
        self.journal.insert_features(feature)
        prediction = create_prediction(self.journal, self.config, snapshot, feature)
        now = datetime.now(UTC)
        beta, beta_failure = self._beta_opportunity(now)
        account = self._account_state()

        if beta_failure:
            self.journal.insert_prediction(prediction)
            decision = no_trade_decision(
                prediction,
                feature,
                account,
                beta_failure,
                now=now,
                payload={"v2": True, "beta_state_url": self.beta_state_url},
            )
            self.journal.insert_decision(decision)
            self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
            self._publish(snapshot, feature, prediction, account)
            return f"prediction={prediction['prediction_id']} action=NO_TRADE reason={beta_failure}"

        assert beta is not None
        prediction = self._attach_beta(prediction, beta)
        self.journal.insert_prediction(prediction)
        chain, options = self.journal.latest_option_chain("SPY")
        if chain and str(chain["captured_at"]) < str(snapshot["captured_at"]):
            options = []

        primary = build_hgb_vertical_candidate(prediction, beta, options)
        primary_candidates = [primary] if primary is not None else []

        # Keep the rich P/Q research engine alive, but never let it compete with
        # the validated primary path until its EV calibration has independently
        # passed forward tests on real captured option chains.
        shadow: list[dict[str, Any]] = []
        if options:
            with contextlib.suppress(Exception):
                shadow = self._mark_shadow(
                    generate_shadow_candidates(
                        self.config,
                        prediction,
                        options,
                        optimizer_config=self.optimizer_config,
                    )
                )
        self.journal.insert_candidates([*primary_candidates, *shadow])

        decision = choose_decision(
            self.config,
            self.journal,
            prediction,
            feature,
            primary_candidates,
            account,
            now=now,
        )

        if decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"} and primary is not None:
            preview_ok, preview_detail = self._preview_fee_gate(primary)
            primary.setdefault("payload", {}).setdefault("execution_preview", {}).update(preview_detail)
            if not preview_ok:
                decision = no_trade_decision(
                    prediction,
                    feature,
                    account,
                    "broker_preview_cost_or_risk_veto",
                    now=now,
                    payload={"v2": True, "candidate": primary, **preview_detail},
                )
            else:
                try:
                    self.execution.execute(decision, primary)
                except Exception as exc:
                    self.journal.alert(
                        "critical", "V2 HGB vertical execution failed", str(exc), "execution"
                    )
                    with contextlib.suppress(Exception):
                        self.publisher.alert(
                            "critical", "V2 HGB vertical execution failed", str(exc), "execution"
                        )

        self.journal.insert_decision(decision)
        self.journal.set_control("last_v2_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, prediction, account)
        return (
            f"prediction={prediction['prediction_id']} action={decision['action']} "
            f"primary={'none' if primary is None else primary['strategy']} shadow={len(shadow)}"
        )

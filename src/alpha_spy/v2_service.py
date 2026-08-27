from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any

from .beta_v2 import attach_beta_v2_state, fetch_beta_v2_state
from .execution import build_multileg_payload
from .features import compute_features
from .prediction import create_prediction
from .risk import choose_decision
from .services import EngineService, append_jsonl
from .strategy_v2 import generate_candidates_v2
from .timeutil import et_now, utc_iso
from .tradier import TradierClient, preview_fees


class V2EngineService(EngineService):
    """Alpha V2: Beta state + full low-friction payoff tournament.

    Beta is context, never strategy authority.  Candidate construction is broad,
    actual quote friction is in the valuation, and broker preview fees are applied
    to the best finalists when the execution account is available.
    """

    def __init__(self, *args: Any, beta_state_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.beta_state_url = (
            beta_state_url
            or os.environ.get("BETA_SPY_STATE_URL")
            or "http://127.0.0.1:8790/api/state"
        ).strip()

    def _record_chain_tape(
        self,
        chain: dict[str, Any] | None,
        options: list[dict[str, Any]],
        prediction: dict[str, Any],
    ) -> str | None:
        if not chain or not options:
            return None
        normalized = [
            {
                "symbol": row.get("symbol"),
                "expiration": row.get("expiration"),
                "strike": row.get("strike"),
                "right": row.get("right"),
                "bid": row.get("bid"),
                "ask": row.get("ask"),
                "midpoint": row.get("midpoint"),
                "bid_size": row.get("bid_size"),
                "ask_size": row.get("ask_size"),
                "volume": row.get("volume"),
                "open_interest": row.get("open_interest"),
                "iv": row.get("iv"),
                "delta": row.get("delta"),
                "gamma": row.get("gamma"),
                "theta": row.get("theta"),
                "vega": row.get("vega"),
            }
            for row in options
        ]
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
        chain_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        append_jsonl(
            self.config.paths.state_root
            / "replay"
            / f"v2-option-chain-{et_now().date().isoformat()}.jsonl",
            {
                "recorded_at": utc_iso(),
                "prediction_id": prediction.get("prediction_id"),
                "chain_snapshot_id": chain.get("chain_snapshot_id"),
                "captured_at": chain.get("captured_at"),
                "expiration": chain.get("expiration"),
                "underlying_price": chain.get("underlying_price"),
                "chain_sha256": chain_hash,
                "options": normalized,
            },
        )
        return chain_hash

    def _apply_preview_fees(self, candidates: list[dict[str, Any]]) -> None:
        eligible = [candidate for candidate in candidates if candidate.get("status") == "ELIGIBLE"][:8]
        if not eligible:
            return
        token = self.config.tradier.access_token.get_secret_value()
        account = self.config.tradier.account_id
        if not token or not account:
            for candidate in eligible:
                candidate.setdefault("payload", {})["broker_preview_fee_status"] = "unavailable"
            return
        try:
            with TradierClient(self.config) as client:
                for candidate in eligible:
                    payload = build_multileg_payload(
                        candidate,
                        quantity=1,
                        price=round(float(candidate.get("entry_price") or 0.0), 2),
                    )
                    preview = client.preview_order(payload)
                    fee = preview_fees(preview)
                    details = candidate.setdefault("payload", {})
                    if fee is None:
                        details["broker_preview_fee_status"] = "missing"
                        continue
                    # Preview describes the opening order.  Reserve the same fee
                    # again for the closing order; actual fills remain authoritative.
                    roundtrip = 2.0 * float(fee)
                    candidate["expected_value"] = float(candidate.get("expected_value") or 0.0) - roundtrip
                    details["doubled_cost_expected_value"] = (
                        float(details.get("doubled_cost_expected_value") or 0.0) - 2.0 * roundtrip
                    )
                    details["broker_preview_open_fees"] = float(fee)
                    details["broker_preview_roundtrip_fee_reserve"] = roundtrip
                    details["broker_preview_fee_status"] = "verified"
                    if (
                        float(candidate["expected_value"]) < self.config.strategy.min_edge_dollars * 100.0
                        or float(details["doubled_cost_expected_value"]) <= 0.0
                    ):
                        candidate["status"] = "REJECTED"
                        candidate["rejection_reason"] = "broker_preview_fees_remove_edge"
        except Exception as exc:
            for candidate in eligible:
                candidate.setdefault("payload", {})["broker_preview_fee_status"] = "error"
                candidate["payload"]["broker_preview_fee_error"] = str(exc)

    def run_once(self) -> str | None:
        self._process_commands()
        snapshot = self.journal.latest_snapshot()
        if not snapshot:
            return None
        last = self.journal.get_control("last_engine_snapshot_id")
        if last == snapshot["snapshot_id"]:
            return None

        quotes = self.journal.snapshot_quotes(snapshot["snapshot_id"])
        feature = compute_features(self.journal, self.config, snapshot, quotes)
        self.journal.insert_features(feature)
        prediction = create_prediction(self.journal, self.config, snapshot, feature)

        beta_state = fetch_beta_v2_state(self.beta_state_url)
        created = datetime.fromisoformat(str(prediction["created_at"]).replace("Z", "+00:00"))
        if beta_state is not None and not beta_state.is_current(created):
            beta_state = None
        prediction = attach_beta_v2_state(prediction, beta_state)
        prediction.setdefault("payload", {})["architecture"] = "alpha-beta-v2-liquidity-first"
        self.journal.insert_prediction(prediction)

        chain, options = self.journal.latest_option_chain("SPY")
        if chain and chain["captured_at"] < snapshot["captured_at"]:
            options = []
        chain_hash = self._record_chain_tape(chain, options, prediction)
        prediction.setdefault("payload", {})["v2_chain_sha256"] = chain_hash

        candidates = generate_candidates_v2(self.config, prediction, options)
        self._apply_preview_fees(candidates)
        candidates.sort(
            key=lambda candidate: (
                candidate.get("status") == "ELIGIBLE",
                float(candidate.get("score") or -1e9),
            ),
            reverse=True,
        )
        self.journal.insert_candidates(candidates)

        account = self._account_state()
        decision = choose_decision(self.config, self.journal, prediction, feature, candidates, account)
        decision.setdefault("payload", {})["architecture"] = "alpha-beta-v2-liquidity-first"
        decision["payload"]["beta_v2"] = prediction.get("payload", {}).get("beta_v2")
        decision["payload"]["chain_sha256"] = chain_hash
        self.journal.insert_decision(decision)

        if decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"} and decision.get("candidate_id"):
            candidate = next(
                (item for item in candidates if item["candidate_id"] == decision["candidate_id"]),
                None,
            )
            if candidate:
                try:
                    self.execution.execute(decision, candidate)
                except Exception as exc:
                    self.journal.alert("critical", "V2 order execution failed", str(exc), "execution")
                    self.publisher.alert("critical", "V2 order execution failed", str(exc), "execution")

        self.journal.set_control("last_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, prediction, account)
        append_jsonl(
            self.config.paths.state_root / "candidates" / f"v2-candidates-{et_now().date().isoformat()}.jsonl",
            {
                "prediction": prediction,
                "feature": feature,
                "chain_sha256": chain_hash,
                "candidates": candidates,
                "decision": decision,
            },
        )
        return f"v2 prediction={prediction['prediction_id']} action={decision['action']}"

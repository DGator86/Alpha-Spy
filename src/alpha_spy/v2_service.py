from __future__ import annotations

import hashlib
import json
from typing import Any

from .config import SuiteConfig
from .execution import build_multileg_payload
from .services import append_jsonl
from .timeutil import et_now, utc_iso
from .tradier import TradierClient, preview_fees


def _raw_quote_value(row: dict[str, Any], direct_key: str, raw_key: str) -> Any:
    direct = row.get(direct_key)
    if direct not in (None, ""):
        return direct
    payload = row.get("payload") or {}
    if isinstance(payload, dict):
        return payload.get(raw_key)
    return None


def record_chain_tape(
    config: SuiteConfig,
    chain: dict[str, Any] | None,
    options: list[dict[str, Any]],
    prediction: dict[str, Any],
) -> str | None:
    """Persist the exact executable chain used by a V2 decision and return its SHA."""
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
            "bid_size": _raw_quote_value(row, "bid_size", "bidsize"),
            "ask_size": _raw_quote_value(row, "ask_size", "asksize"),
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
        config.paths.state_root
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


def apply_preview_fees(
    config: SuiteConfig,
    candidates: list[dict[str, Any]],
    *,
    finalist_count: int = 8,
) -> None:
    """Replace assumed commissions with broker-preview fees on top finalists."""
    eligible = [candidate for candidate in candidates if candidate.get("status") == "ELIGIBLE"][
        :finalist_count
    ]
    if not eligible:
        return
    token = config.tradier.access_token.get_secret_value()
    account = config.tradier.account_id
    if not token or not account:
        for candidate in eligible:
            candidate.setdefault("payload", {})["broker_preview_fee_status"] = "unavailable"
        return
    try:
        with TradierClient(config) as client:
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
                roundtrip = 2.0 * float(fee)
                candidate["expected_value"] = float(candidate.get("expected_value") or 0.0) - roundtrip
                details["doubled_cost_expected_value"] = (
                    float(details.get("doubled_cost_expected_value") or 0.0) - 2.0 * roundtrip
                )
                details["broker_preview_open_fees"] = float(fee)
                details["broker_preview_roundtrip_fee_reserve"] = roundtrip
                details["broker_preview_fee_status"] = "verified"
                if (
                    float(candidate["expected_value"]) < config.strategy.min_edge_dollars * 100.0
                    or float(details["doubled_cost_expected_value"]) <= 0.0
                ):
                    candidate["status"] = "REJECTED"
                    candidate["rejection_reason"] = "broker_preview_fees_remove_edge"
    except Exception as exc:
        for candidate in eligible:
            details = candidate.setdefault("payload", {})
            details["broker_preview_fee_status"] = "error"
            details["broker_preview_fee_error"] = str(exc)

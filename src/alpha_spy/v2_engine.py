from __future__ import annotations

from typing import Any

from .execution import build_multileg_payload
from .tradier import TradierClient, preview_fees
from .v2_services import V2EngineService as _BaseV2EngineService


DEFAULT_BETA_V2_STATE_URL = "http://127.0.0.1:8790/api/state"


class V2EngineService(_BaseV2EngineService):
    """Authoritative Alpha V2 engine with real Beta endpoint and preview costs."""

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
                "cost_source": "quote_drag_plus_pass_through_estimate",
            }

        try:
            payload = build_multileg_payload(candidate, 1, float(candidate["entry_price"]))
            with TradierClient(self.config) as client:
                preview = client.preview_order(payload)
            one_way = preview_fees(preview)
            if one_way is None:
                return True, {
                    "preview": preview,
                    "cost_source": "quote_drag_plus_pass_through_estimate",
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
                "cost_source": "quote_drag_plus_pass_through_estimate",
            }

        net_after_preview = float(candidate.get("expected_value") or 0.0) - roundtrip
        return net_after_preview > 0.0, {
            "preview_roundtrip_fee_estimate": roundtrip,
            "expected_value_after_preview_fees": net_after_preview,
            "cost_source": "tradier_order_preview",
        }

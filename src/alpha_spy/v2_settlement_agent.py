from __future__ import annotations

from typing import Any

from .broker_reconcile import BrokerReconciler
from .v2_settlement import V2SettlementService as _BaseV2SettlementService


class V2SettlementService(_BaseV2SettlementService):
    """Authoritative trader-agent settlement wrapper.

    Evidence-based ADD/scale-in is fully simulated in local paper mode. Increasing
    external broker exposure is deliberately fail-closed in the autonomous research
    runtime; an operator must explicitly redesign that deployment boundary rather
    than inheriting it accidentally from paper logic.
    """

    def _add_position(self, position: dict[str, Any], management) -> str:
        if BrokerReconciler(self.config, self.journal).broker_mode:
            return "add_blocked_autonomous_broker_exposure_paper_only"
        return super()._add_position(position, management)

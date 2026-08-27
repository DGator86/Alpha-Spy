from __future__ import annotations

import gzip
import json

from .streaming_market import StreamingMarketService
from .timeutil import et_now, utc_iso
from .tradier import TradierClient, normalize_option


class V2StreamingMarketService(StreamingMarketService):
    """Production stream plus complete 0DTE SPY-chain evidence capture.

    Streaming constituent/context behavior is inherited unchanged. Only the SPY
    strategy-chain collector is widened: no strike-distance trimming and no row cap.
    Every returned quote is persisted in SQLite and in a compressed append-only tape.
    """

    def _collect_spy_chain(
        self, client: TradierClient, snapshot_id: str, spy_price: float
    ) -> None:
        expirations = client.expirations("SPY")
        today = et_now().date().isoformat()
        if today not in expirations:
            self.journal.alert(
                "warning",
                "No 0DTE SPY expiry",
                "Tradier has no same-session expiration; V2 refuses expiry fallback",
                "market",
            )
            return

        options = sorted(
            [
                normalize_option(row)
                for row in client.option_chain(
                    "SPY", today, self.config.market.option_chain_greeks
                )
            ],
            key=lambda row: (
                str(row.get("right") or ""),
                float(row.get("strike") or 0.0),
            ),
        )
        options = [
            row
            for row in options
            if row.get("symbol") and float(row.get("strike") or 0.0) > 0
        ]
        captured_at = utc_iso()
        chain_id = f"OC-{snapshot_id}"
        chain = {
            "chain_snapshot_id": chain_id,
            "captured_at": captured_at,
            "underlying": "SPY",
            "purpose": "strategy",
            "expiration": today,
            "underlying_price": spy_price,
            "integrity": "VERIFIED" if options else "INCOMPLETE",
            "source": "tradier",
            "payload": {
                "v2_full_chain": True,
                "row_count": len(options),
                "untrimmed": True,
            },
        }
        self.journal.insert_option_chain(chain, options)

        archive = (
            self.config.paths.state_root
            / "market"
            / f"spy-options-full-{et_now().date().isoformat()}.jsonl.gz"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(archive, "at", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        **chain,
                        "options": options,
                    },
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            )

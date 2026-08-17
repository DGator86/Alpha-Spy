from __future__ import annotations

import json
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .config import SuiteConfig
from .db import Journal
from .services import MarketService, StopFlag, _quote_is_stale
from .timeutil import utc_iso, utc_now
from .tradier import TradierClient, normalize_quote
from .universe import Holding


def _event_timestamp(value: Any) -> str:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return utc_iso()
    if raw > 10_000_000_000:
        raw /= 1000.0
    try:
        return datetime.fromtimestamp(raw, tz=UTC).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return utc_iso()


class StreamingMarketService(MarketService):
    """Production real-time market data feeding a separate paper execution account.

    The service intentionally never uses the execution/sandbox token for market data.
    It seeds the state with production REST quotes, consumes the production websocket,
    persists synchronized snapshots, and refreshes option/surface context through the
    production REST API. `run_once()` remains a REST fallback for smoke tests/doctor use.
    """

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.runtime_config = config
        market_config = config.model_copy(deep=True)
        market_config.tradier.environment = "production"
        market_config.tradier.access_token = config.tradier.market_access_token
        market_config.tradier.account_id = ""
        super().__init__(market_config, journal)
        self._cache: dict[str, dict[str, Any]] = {}
        self._previous_close: dict[str, float] = {}
        self._last_stream_event_at: datetime | None = None
        self._stream_event_count = 0
        self._last_chain_refresh = 0.0
        self._last_clock_refresh = 0.0
        self._exchange_state = "unknown"
        self._micro: dict[str, dict[str, float]] = {}
        # Defense in depth: uniquely sequenced timesales are canonical, but a
        # reconnect can replay the same seq. Keep a short per-symbol memory.
        self._timesale_seq: dict[str, deque[int]] = {}
        self._timesale_seq_seen: dict[str, set[int]] = {}

    @property
    def market_token_configured(self) -> bool:
        return bool(self.runtime_config.tradier.market_access_token.get_secret_value())

    def _context_symbols(self) -> list[str]:
        c = self.runtime_config.context
        return list(
            dict.fromkeys(
                [
                    *c.futures_proxies.values(),
                    c.credit_symbol,
                    c.dollar_symbol,
                    *c.rates_symbols,
                    *c.volatility_symbols,
                    *c.sector_symbols,
                ]
            )
        )

    def run_once(self) -> str:
        """REST fallback using the production market-data credential."""
        if not self.market_token_configured:
            if not self.runtime_config.market.demo_when_unconfigured:
                raise RuntimeError("Tradier production market-data token is not configured")
            return self._collect_demo(self.universe.get())
        holdings = self.universe.get()
        return self._collect_production_rest(holdings)

    def _collect_production_rest(self, holdings: list[Holding]) -> str:
        with TradierClient(self.runtime_config, purpose="market") as client:
            clock = client.clock()
            symbols = [h.symbol for h in holdings[: self.config.universe.top_n_quotes]]
            symbols = list(
                dict.fromkeys(["SPY", *self.config.market.include_symbols, *self._context_symbols(), *symbols])
            )
            raw_quotes = client.quotes_chunked(symbols)
            normalized = {q["symbol"]: q for q in map(normalize_quote, raw_quotes) if q["symbol"]}
            self._seed_cache(holdings, normalized)
            snapshot_id = self._persist_snapshot(holdings, client, exchange_state=str(clock.get("state", "unknown")))
            self._refresh_option_context(holdings, client, snapshot_id, force_chain=True)
            self.journal.set_control("market_ready_snapshot_id", snapshot_id)
            return snapshot_id

    def run_forever(self) -> None:
        if not self.runtime_config.tradier.stream_enabled:
            super().run_forever()
            return
        if not self.market_token_configured:
            if self.runtime_config.market.demo_when_unconfigured:
                super().run_forever()
                return
            raise RuntimeError("Tradier production market-data token is required for streaming")

        flag = StopFlag()
        holdings = self.universe.get()
        symbols = [h.symbol for h in holdings[: self.config.universe.top_n_quotes]]
        symbols = list(
            dict.fromkeys(["SPY", *self.config.market.include_symbols, *self._context_symbols(), *symbols])
        )
        delay = 1.0

        while not flag.stopped:
            try:
                with TradierClient(self.runtime_config, purpose="market") as client:
                    self._seed_from_rest(client, holdings, symbols)
                    session_id = client.create_market_session()
                    with connect(
                        self.runtime_config.tradier.stream_url,
                        open_timeout=self.config.tradier.timeout_seconds,
                        close_timeout=5,
                        ping_interval=20,
                        ping_timeout=20,
                        compression=None,
                    ) as websocket:
                        websocket.send(
                            json.dumps(
                                {
                                    "symbols": symbols,
                                    "filter": ["quote", "timesale", "summary"],
                                    "sessionid": session_id,
                                    "linebreak": True,
                                    "validOnly": True,
                                }
                            )
                        )
                        delay = 1.0
                        next_snapshot = time.monotonic()
                        while not flag.stopped:
                            try:
                                raw = websocket.recv(timeout=1.0)
                            except TimeoutError:
                                raw = None
                            if raw is not None:
                                self._ingest_payload(raw)
                            now_mono = time.monotonic()
                            if now_mono >= next_snapshot:
                                started = time.perf_counter()
                                snapshot_id = self._persist_snapshot(holdings, client)
                                session_open = str(self._exchange_state).lower() in {"open", "pre"}
                                if session_open:
                                    self._refresh_option_context(holdings, client, snapshot_id)
                                self.journal.set_control("market_ready_snapshot_id", snapshot_id)
                                latency = (time.perf_counter() - started) * 1000.0
                                age = self._stream_age_seconds()
                                status = "ONLINE" if age <= self.config.market.stream_stale_seconds else "DEGRADED"
                                self.journal.heartbeat(
                                    "market",
                                    status,
                                    latency,
                                    f"stream snapshot={snapshot_id} age={age:.1f}s events={self._stream_event_count}",
                                )
                                interval = self.config.market.stream_snapshot_interval_seconds
                                if not session_open:
                                    interval = max(interval, 300)
                                next_snapshot = now_mono + interval
            except (ConnectionClosed, OSError, RuntimeError, ValueError) as exc:
                self.journal.heartbeat("market", "ERROR", None, str(exc))
                self.journal.alert("critical", "Tradier production stream disconnected", str(exc), "market")
                time.sleep(delay)
                delay = min(30.0, delay * 2.0)

    def _seed_from_rest(
        self,
        client: TradierClient,
        holdings: list[Holding],
        symbols: list[str],
    ) -> None:
        raw_quotes = client.quotes_chunked(symbols)
        normalized = {q["symbol"]: q for q in map(normalize_quote, raw_quotes) if q["symbol"]}
        if "SPY" not in normalized or not normalized["SPY"].get("price"):
            raise RuntimeError("Tradier production REST did not return a usable SPY quote")
        self._seed_cache(holdings, normalized)
        clock = client.clock()
        self._exchange_state = str(clock.get("state", "unknown"))
        self._last_clock_refresh = time.monotonic()

    def _seed_cache(self, holdings: list[Holding], normalized: dict[str, dict[str, Any]]) -> None:
        weight_map = {h.symbol: h for h in holdings}
        for symbol, quote in normalized.items():
            holding = weight_map.get(symbol)
            payload = dict(quote.get("payload") or {})
            close = payload.get("close")
            try:
                close_value = float(close) if close is not None else None
            except (TypeError, ValueError):
                close_value = None
            if close_value and close_value > 0:
                self._previous_close[symbol] = close_value
            self._cache[symbol] = {
                **quote,
                "weight": holding.weight if holding else 0.0,
                "sector": holding.sector if holding else "ETF",
                "stale": False,
            }
        self._last_stream_event_at = utc_now()

    def _micro_state(self, symbol: str) -> dict[str, float]:
        return self._micro.setdefault(
            symbol,
            {
                "trade_count": 0.0,
                "trade_volume": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "neutral_volume": 0.0,
                "quote_updates": 0.0,
                "spread_bps_sum": 0.0,
                "spread_samples": 0.0,
                "large_trade_volume": 0.0,
                "max_trade_size": 0.0,
            },
        )

    def _record_quote_micro(self, symbol: str, current: dict[str, Any]) -> None:
        state = self._micro_state(symbol)
        state["quote_updates"] += 1.0
        bid = float(current.get("bid") or 0.0)
        ask = float(current.get("ask") or 0.0)
        midpoint = (bid + ask) / 2.0 if bid > 0 and ask >= bid else 0.0
        if midpoint > 0:
            state["spread_bps_sum"] += (ask - bid) / midpoint * 10_000.0
            state["spread_samples"] += 1.0

    def _record_trade_micro(self, symbol: str, current: dict[str, Any], event: dict[str, Any]) -> None:
        state = self._micro_state(symbol)
        try:
            size = max(float(event.get("size") or 0.0), 0.0)
        except (TypeError, ValueError):
            size = 0.0
        try:
            price = float(event.get("last") or event.get("price") or current.get("price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        bid = float(event.get("bid") or current.get("bid") or 0.0)
        ask = float(event.get("ask") or current.get("ask") or 0.0)
        state["trade_count"] += 1.0
        state["trade_volume"] += size
        state["max_trade_size"] = max(state["max_trade_size"], size)
        # A transparent aggressor proxy: at/above ask=buy, at/below bid=sell.
        # Trades inside the spread remain neutral rather than being guessed.
        if ask > 0 and price >= ask:
            state["buy_volume"] += size
        elif bid > 0 and price <= bid:
            state["sell_volume"] += size
        else:
            state["neutral_volume"] += size
        if size >= 500:
            state["large_trade_volume"] += size

    def _micro_snapshot(self) -> dict[str, dict[str, float]]:
        output: dict[str, dict[str, float]] = {}
        for symbol, state in self._micro.items():
            directional = state["buy_volume"] + state["sell_volume"]
            total = state["trade_volume"]
            output[symbol] = {
                **state,
                "order_flow_imbalance": (
                    (state["buy_volume"] - state["sell_volume"]) / directional
                    if directional > 0
                    else 0.0
                ),
                "average_spread_bps": (
                    state["spread_bps_sum"] / state["spread_samples"]
                    if state["spread_samples"] > 0
                    else 0.0
                ),
                "large_trade_ratio": state["large_trade_volume"] / total if total > 0 else 0.0,
            }
        return output

    def _ingest_payload(self, raw: Any) -> None:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.journal.alert("warning", "Unparseable Tradier stream event", line[:500], "market")
                continue
            if not isinstance(event, dict):
                continue
            symbol = str(event.get("symbol") or "")
            if not symbol:
                continue
            current = self._cache.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "price": None,
                    "bid": None,
                    "ask": None,
                    "change_pct": 0.0,
                    "volume": 0.0,
                    "quote_timestamp": utc_iso(),
                    "weight": 0.0,
                    "sector": "Unknown",
                    "stale": False,
                    "payload": {},
                },
            )
            event_type = str(event.get("type") or "").lower()
            if event_type == "quote":
                self._set_float(current, "bid", event.get("bid"))
                self._set_float(current, "ask", event.get("ask"))
                bid = current.get("bid")
                ask = current.get("ask")
                if bid and ask:
                    current["price"] = (float(bid) + float(ask)) / 2.0
                current["quote_timestamp"] = _event_timestamp(
                    event.get("askdate") or event.get("biddate") or event.get("date")
                )
                self._record_quote_micro(symbol, current)
            elif event_type in {"trade", "tradex"}:
                # Last-price/volume only. Tradier's trade and timesale families
                # describe the same print; counting both double-counts
                # buy/sell volume and OFI. Canonical prints are timesales.
                self._set_float(current, "price", event.get("price") or event.get("last"))
                self._set_float(current, "volume", event.get("cvol"))
                current["quote_timestamp"] = _event_timestamp(event.get("date"))
            elif event_type == "timesale":
                if event.get("cancel") or event.get("correction"):
                    continue
                if self._timesale_already_seen(symbol, event.get("seq")):
                    continue
                self._set_float(current, "price", event.get("last"))
                self._set_float(current, "bid", event.get("bid"))
                self._set_float(current, "ask", event.get("ask"))
                current["quote_timestamp"] = _event_timestamp(event.get("date"))
                self._record_trade_micro(symbol, current, event)
            elif event_type == "summary":
                prev_close = event.get("prevClose")
                try:
                    prev_close_value = float(prev_close) if prev_close is not None else None
                except (TypeError, ValueError):
                    prev_close_value = None
                if prev_close_value and prev_close_value > 0:
                    self._previous_close[symbol] = prev_close_value
            else:
                continue

            price = current.get("price")
            previous_close = self._previous_close.get(symbol)
            if price is not None and previous_close:
                current["change_pct"] = float(price) / previous_close - 1.0
            current["payload"] = {"stream_event": event}
            current["stale"] = False
            self._last_stream_event_at = utc_now()
            self._stream_event_count += 1

    def _timesale_already_seen(self, symbol: str, raw_seq: Any) -> bool:
        try:
            seq = int(raw_seq)
        except (TypeError, ValueError):
            return False
        seen = self._timesale_seq_seen.setdefault(symbol, set())
        order = self._timesale_seq.setdefault(symbol, deque())
        if seq in seen:
            return True
        seen.add(seq)
        order.append(seq)
        while len(order) > 4096:
            seen.discard(order.popleft())
        return False

    @staticmethod
    def _set_float(target: dict[str, Any], key: str, value: Any) -> None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return
        if parsed > 0 or key == "volume":
            target[key] = parsed

    def _stream_age_seconds(self) -> float:
        if self._last_stream_event_at is None:
            return float("inf")
        return max(0.0, (utc_now() - self._last_stream_event_at).total_seconds())

    def _refresh_clock(self, client: TradierClient) -> None:
        now_mono = time.monotonic()
        if now_mono - self._last_clock_refresh < 60.0:
            return
        clock = client.clock()
        self._exchange_state = str(clock.get("state", "unknown"))
        self._last_clock_refresh = now_mono

    def _persist_snapshot(
        self,
        holdings: list[Holding],
        client: TradierClient,
        *,
        exchange_state: str | None = None,
    ) -> str:
        if exchange_state is not None:
            self._exchange_state = exchange_state
            self._last_clock_refresh = time.monotonic()
        else:
            self._refresh_clock(client)

        weight_map = {h.symbol: h for h in holdings}
        quotes: list[dict[str, Any]] = []
        covered_weight = 0.0
        for symbol, cached in list(self._cache.items()):
            holding = weight_map.get(symbol)
            quote = dict(cached)
            quote["weight"] = holding.weight if holding else 0.0
            quote["sector"] = holding.sector if holding else "ETF"
            stale = _quote_is_stale(
                quote.get("quote_timestamp"),
                self.config.market.quote_stale_seconds,
            )
            quote["stale"] = stale
            if holding and quote.get("price") is not None and not stale:
                covered_weight += holding.weight
            quotes.append(quote)

        spy = next((q for q in quotes if q.get("symbol") == "SPY"), None)
        stream_age = self._stream_age_seconds()
        stream_fresh = stream_age <= self.config.market.stream_stale_seconds
        spy_fresh = bool(spy and spy.get("price") and not spy.get("stale"))
        integrity = (
            "VERIFIED"
            if stream_fresh
            and spy_fresh
            and covered_weight >= self.config.universe.minimum_covered_weight
            else "INCOMPLETE"
        )
        snapshot_id = self._save_snapshot(
            exchange_state=self._exchange_state,
            source="tradier_production_stream",
            quotes=quotes,
            covered_weight=covered_weight,
            integrity=integrity,
            extra={
                "market_environment": "production",
                "execution_environment": self.runtime_config.tradier.environment,
                "stream_enabled": True,
                "stream_age_seconds": stream_age,
                "stream_event_count": self._stream_event_count,
                "microstructure": self._micro_snapshot(),
                "rate_state": client.rate_state.__dict__,
            },
        )
        self._micro = {}
        return snapshot_id

    def _refresh_option_context(
        self,
        holdings: list[Holding],
        client: TradierClient,
        snapshot_id: str,
        *,
        force_chain: bool = False,
    ) -> None:
        spy = self._cache.get("SPY") or {}
        spy_price = float(spy.get("price") or 0.0)
        if spy_price <= 0:
            return
        now_mono = time.monotonic()
        if force_chain or now_mono - self._last_chain_refresh >= self.config.market.option_chain_refresh_seconds:
            self._collect_spy_chain(client, snapshot_id, spy_price)
            self._last_chain_refresh = now_mono
        self._collect_constituent_iv_context(client, snapshot_id, holdings, self._cache)

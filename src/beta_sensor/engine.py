from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from spy_platform.raw_market import MarketEvent

from .auction import SessionCvd, SpyAuctionState, attach_cvd
from .breadth import BreadthAggregator
from .flow import FlowAccumulator
from .forecast import OnlineForecastStack
from .indicators import SymbolIndicatorState
from .models import FlowFeatures, HoldingMeta, HorizonForecast, MarketFactors, MinuteBar, QuoteTop, SymbolFeatures, TradePrint


def _minute_key(timestamp: datetime) -> datetime:
    return timestamp.astimezone(UTC).replace(second=0, microsecond=0)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class _BarBuilder:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    trade_count: int = 0
    pv: float = 0.0

    @classmethod
    def start(cls, trade: TradePrint) -> _BarBuilder:
        return cls(
            symbol=trade.symbol,
            timestamp=_minute_key(trade.timestamp),
            open=trade.price,
            high=trade.price,
            low=trade.price,
            close=trade.price,
            volume=trade.size,
            trade_count=1,
            pv=trade.price * trade.size,
        )

    def add(self, trade: TradePrint) -> None:
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.close = trade.price
        self.volume += trade.size
        self.trade_count += 1
        self.pv += trade.price * trade.size

    def finish(self) -> MinuteBar:
        return MinuteBar(
            symbol=self.symbol,
            timestamp=self.timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            trade_count=self.trade_count,
            vwap=self.pv / self.volume if self.volume > 0 else None,
        )


@dataclass(frozen=True)
class BetaSensorSnapshot:
    timestamp: datetime
    factors: MarketFactors
    forecasts: tuple[HorizonForecast, ...]
    symbols: tuple[SymbolFeatures, ...]
    source_commit: str = "6fc415edc99bb04084a199d336ea5711b568fa35"
    authority: str = "market_sensor_only_no_trade_authority"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BetaSensorEngine:
    """Constituent/tape market-sensing engine with no trade-decision layer.

    Shared immutable MarketEvent objects are the preferred input. The engine emits
    constituent features, breadth/sector factors, and causal 5/15/30m forecasts.
    It has no strategy, option, account, position, risk, order, or broker concepts.
    """

    def __init__(
        self,
        holdings: Iterable[HoldingMeta],
        *,
        forecast_stack: OnlineForecastStack | None = None,
    ) -> None:
        metas = tuple(holdings)
        self.holdings = {item.symbol: item for item in metas}
        self.expected_symbol_count = len(metas)
        self.states: dict[str, SymbolIndicatorState] = {
            item.symbol: SymbolIndicatorState(item) for item in metas
        }
        self.states.setdefault(
            "SPY",
            SymbolIndicatorState(HoldingMeta("SPY", "ETF", 0.0, "SPDR S&P 500 ETF")),
        )
        self.flows: dict[str, FlowAccumulator] = defaultdict(FlowAccumulator)
        self.builders: dict[str, _BarBuilder] = {}
        self.flow_overrides: dict[str, FlowFeatures] = {}
        self.aggregator = BreadthAggregator()
        self.forecasts = forecast_stack or OnlineForecastStack()
        self.auction = SpyAuctionState()
        self.cvd: dict[str, SessionCvd] = defaultdict(SessionCvd)

    def set_universe(self, holdings: Iterable[HoldingMeta]) -> None:
        metas = tuple(holdings)
        self.holdings = {item.symbol: item for item in metas}
        self.expected_symbol_count = len(metas)
        for meta in metas:
            if meta.symbol not in self.states:
                self.states[meta.symbol] = SymbolIndicatorState(meta)
            elif self.states[meta.symbol].meta != meta:
                old = self.states[meta.symbol]
                replacement = SymbolIndicatorState(meta, max_bars=old.max_bars)
                replacement.bars.extend(old.bars)
                replacement.session_pv = old.session_pv
                replacement.session_volume = old.session_volume
                replacement.session_date = old.session_date
                self.states[meta.symbol] = replacement

    def add_bar(self, bar: MinuteBar, *, flow: FlowFeatures | None = None) -> None:
        state = self.states.get(bar.symbol)
        if state is None:
            meta = self.holdings.get(bar.symbol, HoldingMeta(bar.symbol, "Unknown", 0.0, bar.symbol))
            state = self.states.setdefault(bar.symbol, SymbolIndicatorState(meta))
        state.add_bar(bar)
        if flow is not None:
            self.flow_overrides[bar.symbol] = flow

    def apply_quote(self, quote: QuoteTop) -> None:
        self.flows[quote.symbol].on_quote(quote)

    def apply_print(self, trade: TradePrint) -> None:
        side = self.flows[trade.symbol].on_trade(trade)
        self.cvd[trade.symbol].on_trade(trade, side)
        if trade.symbol == "SPY":
            self.auction.on_trade(trade, side)

    def on_trade(self, trade: TradePrint) -> None:
        self.apply_print(trade)
        bucket = _minute_key(trade.timestamp)
        current = self.builders.get(trade.symbol)
        if current is None:
            self.builders[trade.symbol] = _BarBuilder.start(trade)
        elif current.timestamp == bucket:
            current.add(trade)
        elif current.timestamp < bucket:
            self.add_bar(current.finish())
            self.builders[trade.symbol] = _BarBuilder.start(trade)

    def flush_completed_bars(self, timestamp: datetime) -> None:
        boundary = _minute_key(timestamp)
        for symbol, builder in list(self.builders.items()):
            if builder.timestamp < boundary:
                self.add_bar(builder.finish())
                del self.builders[symbol]

    def ingest_event(self, event: MarketEvent) -> None:
        timestamp = _parse_time(event.event_timestamp)
        payload = event.payload
        if event.event_type == "QUOTE":
            bid = _float(payload.get("bid"))
            ask = _float(payload.get("ask"))
            if bid is None or ask is None or bid <= 0 or ask < bid:
                return
            self.apply_quote(
                QuoteTop(
                    symbol=event.symbol,
                    timestamp=timestamp,
                    bid=bid,
                    ask=ask,
                    bid_size=_float(payload.get("bid_size") or payload.get("bidsz")),
                    ask_size=_float(payload.get("ask_size") or payload.get("asksz")),
                )
            )
            return
        if event.event_type in {"TIMESALE", "TRADE"}:
            if bool(payload.get("cancel")) or bool(payload.get("correction")):
                return
            price = _float(payload.get("price") or payload.get("last"))
            size = _float(payload.get("size"))
            if price is None or size is None or price <= 0 or size <= 0:
                return
            self.on_trade(
                TradePrint(
                    symbol=event.symbol,
                    timestamp=timestamp,
                    price=price,
                    size=size,
                    bid=_float(payload.get("bid")),
                    ask=_float(payload.get("ask")),
                    sequence=_int(payload.get("seq") or payload.get("sequence") or event.sequence),
                )
            )
            return
        if event.event_type == "BAR":
            try:
                bar = MinuteBar(
                    symbol=event.symbol,
                    timestamp=timestamp,
                    open=float(payload["open"]),
                    high=float(payload["high"]),
                    low=float(payload["low"]),
                    close=float(payload["close"]),
                    volume=float(payload.get("volume") or 0.0),
                    trade_count=int(payload.get("trade_count") or 0),
                    vwap=_float(payload.get("vwap")),
                )
            except (KeyError, TypeError, ValueError):
                return
            self.add_bar(bar)

    def build_snapshot(self, timestamp: datetime) -> BetaSensorSnapshot | None:
        self.flush_completed_bars(timestamp)
        symbol_features: list[SymbolFeatures] = []
        for symbol, state in self.states.items():
            flow = self.flow_overrides.get(symbol) or self.flows[symbol].snapshot(now=timestamp)
            cvd = self.cvd[symbol].features(timestamp)
            if symbol == "SPY" and state.bars:
                auction = attach_cvd(self.auction.features(state.bars[-1].close, timestamp), cvd)
            else:
                auction = cvd
            feature = state.features(flow, timestamp, auction=auction)
            if feature is not None:
                symbol_features.append(feature)

        factors = self.aggregator.aggregate(
            symbol_features,
            timestamp=timestamp,
            expected_symbol_count=self.expected_symbol_count,
        )
        spy = next((item for item in symbol_features if item.symbol == "SPY"), None)
        if spy is None or spy.close <= 0:
            return None
        forecasts = self.forecasts.step(timestamp, factors, spy.close)
        snapshot = BetaSensorSnapshot(
            timestamp=timestamp,
            factors=factors,
            forecasts=forecasts,
            symbols=tuple(symbol_features),
        )
        for flow in self.flows.values():
            flow.reset()
        self.flow_overrides.clear()
        return snapshot

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import numpy as np

from .config import SuiteConfig
from .db import Journal
from .timeutil import ET


@dataclass(frozen=True)
class InputObservation:
    name: str
    value: float | None
    source: str
    representation: str
    symbol: str | None
    stale: bool
    requiredness: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketContext:
    observations: tuple[InputObservation, ...]
    coverage: float
    required_failures: tuple[str, ...]
    confidence_issues: tuple[str, ...]
    signals: dict[str, Any]

    @property
    def required_ok(self) -> bool:
        return not self.required_failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage,
            "required_ok": self.required_ok,
            "required_failures": list(self.required_failures),
            "confidence_issues": list(self.confidence_issues),
            "observations": [item.as_dict() for item in self.observations],
            "signals": self.signals,
        }


def _quote_map(quotes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("symbol") or ""): row for row in quotes if row.get("symbol")}


def _value(row: dict[str, Any] | None, key: str = "change_pct") -> float | None:
    if not row:
        return None
    raw = row.get(key)
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _obs(
    qmap: dict[str, dict[str, Any]],
    *,
    name: str,
    symbol: str,
    representation: str,
    requiredness: str,
    key: str = "change_pct",
) -> InputObservation:
    row = qmap.get(symbol)
    return InputObservation(
        name=name,
        value=_value(row, key),
        source="tradier_production_stream" if row else "missing",
        representation=representation,
        symbol=symbol,
        stale=bool((row or {}).get("stale", True)),
        requiredness=requiredness,
    )




def _internal_market_signals(
    journal: Journal,
    snapshot: dict[str, Any],
    quotes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replayable TICK/TRIN/auction proxies derived only from captured constituents.

    These are deliberately labelled *internal proxies*.  They are not NYSE TICK or
    TRIN feeds and they are not exchange auction imbalance messages.  Their purpose
    is to preserve the information class using data Alpha-SPY actually owns.
    """
    current = {
        str(row.get("symbol")): row
        for row in quotes
        if float(row.get("weight") or 0.0) > 0 and row.get("price") is not None
    }
    previous: dict[str, float] = {}
    with journal.session() as con:
        prior = con.execute(
            """
            SELECT snapshot_id,captured_at FROM market_snapshots
            WHERE captured_at < ? ORDER BY captured_at DESC LIMIT 1
            """,
            (str(snapshot.get("captured_at") or ""),),
        ).fetchone()
        if prior:
            try:
                current_day = datetime.fromisoformat(str(snapshot.get("captured_at") or "").replace("Z", "+00:00")).astimezone(ET).date()
                prior_day = datetime.fromisoformat(str(prior["captured_at"]).replace("Z", "+00:00")).astimezone(ET).date()
            except ValueError:
                current_day = prior_day = None
            if current_day is not None and prior_day == current_day:
                rows = con.execute(
                    "SELECT symbol,price FROM snapshot_quotes WHERE snapshot_id=? AND weight>0 AND price IS NOT NULL",
                    (prior["snapshot_id"],),
                ).fetchall()
                previous = {str(row["symbol"]): float(row["price"]) for row in rows}

    up = down = unchanged = 0
    advances = declines = 0
    advance_volume = decline_volume = 0.0
    for symbol, row in current.items():
        session_return = _value(row)
        volume = max(float(row.get("volume") or 0.0), 0.0)
        if session_return is not None:
            if session_return > 0:
                advances += 1
                advance_volume += volume
            elif session_return < 0:
                declines += 1
                decline_volume += volume
        prior_price = previous.get(symbol)
        price = float(row.get("price") or 0.0)
        if prior_price is None or prior_price <= 0 or price <= 0:
            continue
        change = price - prior_price
        if change > 1e-12:
            up += 1
        elif change < -1e-12:
            down += 1
        else:
            unchanged += 1

    tick_n = up + down + unchanged
    tick_raw = up - down if tick_n else 0
    tick_normalized = tick_raw / tick_n if tick_n else None
    ad_n = advances + declines
    ad_normalized = (advances - declines) / ad_n if ad_n else None
    trin_proxy = None
    if declines > 0 and decline_volume > 0 and advance_volume > 0:
        ad_ratio = advances / declines
        volume_ratio = advance_volume / decline_volume
        if volume_ratio > 0:
            trin_proxy = ad_ratio / volume_ratio

    # Session VWAP/profile proxy from one-minute SPY snapshots. `volume` is the
    # cumulative session volume from the quote feed; positive differences are the
    # captured interval volume.  This remains deterministic in replay.
    captured_raw = str(snapshot.get("captured_at") or "")
    try:
        captured = datetime.fromisoformat(captured_raw.replace("Z", "+00:00"))
        local_date = captured.astimezone(ET).date()
    except ValueError:
        local_date = None
    history = journal.quote_history_as_of("SPY", captured_raw, limit=500) if captured_raw else []
    same_day = []
    for row in history:
        try:
            row_date = datetime.fromisoformat(str(row["captured_at"]).replace("Z", "+00:00")).astimezone(ET).date()
        except (KeyError, ValueError):
            continue
        if local_date is None or row_date == local_date:
            same_day.append(row)
    weighted_prices: list[float] = []
    weights: list[float] = []
    prior_volume: float | None = None
    for row in same_day:
        try:
            price = float(row.get("price") or 0.0)
            cumulative_volume = float(row.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or cumulative_volume < 0:
            continue
        interval_volume = 0.0 if prior_volume is None else max(cumulative_volume - prior_volume, 0.0)
        prior_volume = cumulative_volume
        if interval_volume > 0:
            weighted_prices.append(price)
            weights.append(interval_volume)
    auction_vwap = None
    value_low = value_high = None
    vwap_distance_bps = None
    auction_location = "unknown"
    spy_price = float(snapshot.get("spy_price") or 0.0)
    if weights and sum(weights) > 0:
        px = np.asarray(weighted_prices, dtype=float)
        wt = np.asarray(weights, dtype=float)
        wt = wt / wt.sum()
        auction_vwap = float(np.dot(wt, px))
        variance = float(np.dot(wt, np.square(px - auction_vwap)))
        profile_sigma = max(float(np.sqrt(max(variance, 0.0))), 0.01)
        value_low = auction_vwap - profile_sigma
        value_high = auction_vwap + profile_sigma
        if spy_price > 0:
            vwap_distance_bps = (spy_price / auction_vwap - 1.0) * 10_000.0
            auction_location = "above_value" if spy_price > value_high else "below_value" if spy_price < value_low else "inside_value"

    opening_prices = [float(row.get("price") or 0.0) for row in same_day[:30] if float(row.get("price") or 0.0) > 0]
    opening_range_position = None
    if len(opening_prices) >= 5 and spy_price > 0:
        low = min(opening_prices)
        high = max(opening_prices)
        if high > low:
            opening_range_position = float(np.clip((spy_price - low) / (high - low), -1.0, 2.0))

    return {
        "internal_tick_raw": tick_raw,
        "internal_tick_normalized": tick_normalized,
        "advance_decline_normalized": ad_normalized,
        "trin_proxy": trin_proxy,
        "auction_vwap": auction_vwap,
        "auction_vwap_distance_bps": vwap_distance_bps,
        "auction_value_low": value_low,
        "auction_value_high": value_high,
        "auction_location": auction_location,
        "opening_range_position": opening_range_position,
        "breadth_representation": "SPY_constituent_internal_TICK_TRIN_proxies",
        "auction_representation": "captured_SPY_session_volume_profile_proxy",
    }


def build_market_context(
    journal: Journal,
    config: SuiteConfig,
    snapshot: dict[str, Any],
    quotes: list[dict[str, Any]],
) -> MarketContext:
    """Build a transparent direct/proxy context state from the real-time tape.

    Tradier's market-data API is equities/options/indices, not a futures feed. ES/NQ/RTY
    are therefore represented by SPY/QQQ/IWM and explicitly tagged `cash_proxy`. The
    same rule applies to Treasury-state proxies (SHY/IEF/TLT). This makes the runtime
    usable now without mislabeling a proxy as a direct institutional feed.
    """
    qmap = _quote_map(quotes)
    observations: list[InputObservation] = []
    c = config.context

    for name, symbol in c.futures_proxies.items():
        observations.append(
            _obs(
                qmap,
                name=f"{name}_proxy_return",
                symbol=symbol,
                representation="cash_proxy",
                requiredness="confidence",
            )
        )
    observations.append(
        _obs(
            qmap,
            name="credit_impulse",
            symbol=c.credit_symbol,
            representation="ETF_proxy",
            requiredness="required" if c.require_credit_proxy else "confidence",
        )
    )
    observations.append(
        _obs(
            qmap,
            name="dollar_impulse",
            symbol=c.dollar_symbol,
            representation="ETF_proxy",
            requiredness="required" if c.require_dollar_proxy else "confidence",
        )
    )
    for symbol in c.rates_symbols:
        observations.append(
            _obs(
                qmap,
                name=f"rates_proxy_{symbol}",
                symbol=symbol,
                representation="Treasury_ETF_proxy",
                requiredness="confidence",
            )
        )
    for symbol in c.volatility_symbols:
        observations.append(
            _obs(
                qmap,
                name=f"volatility_{symbol}",
                symbol=symbol,
                representation="direct_index",
                requiredness=(
                    "required"
                    if c.require_volatility_index and symbol == c.volatility_symbols[0]
                    else "confidence"
                ),
                key="price",
            )
        )
    sector_returns = []
    for symbol in c.sector_symbols:
        observation = _obs(
            qmap,
            name=f"sector_{symbol}",
            symbol=symbol,
            representation="sector_ETF",
            requiredness="confidence",
        )
        observations.append(observation)
        if observation.value is not None and not observation.stale:
            sector_returns.append(observation.value)

    required_failures = [
        item.name for item in observations if item.requiredness == "required" and (item.value is None or item.stale)
    ]
    available = [item for item in observations if item.value is not None and not item.stale]
    coverage = len(available) / max(len(observations), 1)
    confidence_issues = [
        item.name for item in observations if item.requiredness == "confidence" and (item.value is None or item.stale)
    ]
    if coverage < c.minimum_context_coverage:
        confidence_issues.append("context_coverage_below_minimum")

    spy = _value(qmap.get("SPY")) or 0.0
    qqq = _value(qmap.get("QQQ")) or 0.0
    iwm = _value(qmap.get("IWM")) or 0.0
    hyg = _value(qmap.get(c.credit_symbol))
    uup = _value(qmap.get(c.dollar_symbol))
    shy = _value(qmap.get("SHY"))
    ief = _value(qmap.get("IEF"))
    tlt = _value(qmap.get("TLT"))
    vix = _value(qmap.get("VIX"), "price")
    vix9d = _value(qmap.get("VIX9D"), "price")
    vix3m = _value(qmap.get("VIX3M"), "price")

    payload = snapshot.get("payload", {})
    micro = payload.get("microstructure", {}) if isinstance(payload, dict) else {}
    spy_micro = micro.get("SPY", {}) if isinstance(micro, dict) else {}
    order_flow_imbalance = float(spy_micro.get("order_flow_imbalance") or 0.0)
    spread_bps = float(spy_micro.get("average_spread_bps") or 0.0)
    large_trade_ratio = float(spy_micro.get("large_trade_ratio") or 0.0)

    internal = _internal_market_signals(journal, snapshot, quotes)
    signals = {
        "risk_beta_spread": 0.5 * ((qqq - spy) + (iwm - spy)),
        "large_small_spread": qqq - iwm,
        "credit_impulse": hyg,
        "dollar_impulse": uup,
        "rates_curve_proxy": None if ief is None or tlt is None else tlt - ief,
        "front_rates_proxy": shy,
        "vix_spot": vix,
        "vix_term_slope": None if vix9d is None or vix3m is None or vix9d <= 0 else vix3m / vix9d - 1.0,
        "sector_breadth": float(np.mean(np.asarray(sector_returns) > 0)) if sector_returns else None,
        "sector_dispersion": float(np.std(sector_returns, ddof=1)) if len(sector_returns) > 1 else None,
        "order_flow_imbalance": order_flow_imbalance,
        "average_spread_bps": spread_bps,
        "large_trade_ratio": large_trade_ratio,
        "futures_representation": "cash_proxies_SPY_QQQ_IWM",
        "rates_representation": "Treasury_ETF_proxies_SHY_IEF_TLT",
        **internal,
    }
    return MarketContext(
        observations=tuple(observations),
        coverage=float(coverage),
        required_failures=tuple(required_failures),
        confidence_issues=tuple(dict.fromkeys(confidence_issues)),
        signals=signals,
    )

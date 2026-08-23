from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from .config import SuiteConfig


class TradierError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class RateState:
    allowed: int | None = None
    used: int | None = None
    available: int | None = None
    expiry_ms: int | None = None


class TradierClient:
    def __init__(self, config: SuiteConfig, *, purpose: Literal["execution", "market"] = "execution"):
        self.config = config
        self.purpose = purpose
        if purpose == "market":
            self.token = config.tradier.market_access_token.get_secret_value()
            self.account_id = ""
            self.base_url = config.tradier.market_base_url.rstrip("/")
        else:
            self.token = config.tradier.access_token.get_secret_value()
            self.account_id = config.tradier.account_id
            self.base_url = config.tradier.base_url.rstrip("/")
        self.rate_state = RateState()
        self.client = httpx.Client(
            timeout=config.tradier.timeout_seconds,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "alpha-spy/3.0.0",
            },
            follow_redirects=True,
        )

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> TradierClient:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _capture_rate(self, response: httpx.Response) -> None:
        def parse(name: str) -> int | None:
            value = response.headers.get(name)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        self.rate_state = RateState(
            allowed=parse("X-Ratelimit-Allowed"),
            used=parse("X-Ratelimit-Used"),
            available=parse("X-Ratelimit-Available"),
            expiry_ms=parse("X-Ratelimit-Expiry"),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        if not self.configured:
            raise TradierError("Tradier token is not configured")
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                response = self.client.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                    if data is not None
                    else None,
                )
                self._capture_rate(response)
                if response.status_code == 429:
                    wait = min(60.0, 2.0**attempt)
                    if self.rate_state.expiry_ms:
                        wait = max(wait, self.rate_state.expiry_ms / 1000 - time.time() + 0.25)
                    time.sleep(max(0.25, wait))
                    continue
                if response.status_code >= 400:
                    body = (response.text or "")[:4000]
                    last_error = TradierError(
                        f"Tradier {response.status_code} {method} {path}: {body}",
                        status_code=response.status_code,
                        body=body,
                    )
                    if 400 <= response.status_code < 500 and response.status_code != 429:
                        if attempt >= 2:
                            break
                    elif attempt >= retries:
                        break
                    time.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)))
                    continue
                payload = response.json()
                if isinstance(payload, dict) and payload.get("errors"):
                    raise TradierError(str(payload["errors"]), body=str(payload["errors"]))
                return payload if isinstance(payload, dict) else {"data": payload}
            except (httpx.HTTPError, ValueError, TradierError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)))
        if isinstance(last_error, TradierError):
            raise last_error
        raise TradierError(f"Tradier request failed {method} {path}: {last_error}")

    @staticmethod
    def _as_list(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [value]
        return []

    def create_market_session(self) -> str:
        if self.purpose != "market":
            raise TradierError("market streaming session requires purpose='market'")
        payload = self.request("POST", "/markets/events/session")
        session_id = payload.get("stream", {}).get("sessionid") or payload.get("sessionid")
        if not session_id:
            raise TradierError("Tradier did not return a market streaming session id")
        return str(session_id)

    def profile(self) -> dict[str, Any]:
        return self.request("GET", "/user/profile")

    def balances(self) -> dict[str, Any]:
        if not self.account_id:
            raise TradierError("Tradier account id is not configured")
        return self.request("GET", f"/accounts/{self.account_id}/balances")

    def positions(self) -> list[dict[str, Any]]:
        if not self.account_id:
            return []
        payload = self.request("GET", f"/accounts/{self.account_id}/positions")
        block = payload.get("positions") if isinstance(payload, dict) else None
        if not isinstance(block, dict):
            return []
        return self._as_list(block.get("position"))

    def orders(self, *, include_tags: bool = True, limit: int = 1000) -> list[dict[str, Any]]:
        if not self.account_id:
            return []
        payload = self.request(
            "GET",
            f"/accounts/{self.account_id}/orders",
            params={"includeTags": str(bool(include_tags)).lower(), "limit": max(25, int(limit))},
        )
        block = payload.get("orders") if isinstance(payload, dict) else None
        if not isinstance(block, dict):
            return []
        return self._as_list(block.get("order"))

    def order(self, order_id: str | int) -> dict[str, Any]:
        payload = self.request(
            "GET",
            f"/accounts/{self.account_id}/orders/{order_id}",
            params={"includeTags": "true"},
        )
        return payload.get("order", payload)

    def clock(self) -> dict[str, Any]:
        payload = self.request("GET", "/markets/clock")
        return payload.get("clock", payload)

    def calendar(self, year: int, month: int) -> dict[str, Any]:
        payload = self.request("GET", "/markets/calendar", params={"year": year, "month": month})
        return payload.get("calendar", payload)

    def quotes(self, symbols: Iterable[str], greeks: bool = False) -> list[dict[str, Any]]:
        symbols = [str(s) for s in symbols if str(s)]
        if not symbols:
            return []
        payload = self.request(
            "POST",
            "/markets/quotes",
            data={"symbols": ",".join(symbols), "greeks": str(bool(greeks)).lower()},
        )
        quote = payload.get("quotes", {}).get("quote") if payload.get("quotes") else None
        return self._as_list(quote)

    def quotes_chunked(self, symbols: Iterable[str], greeks: bool = False) -> list[dict[str, Any]]:
        symbols = list(dict.fromkeys(symbols))
        chunk = max(1, self.config.tradier.market_data_chunk_size)
        output: list[dict[str, Any]] = []
        for index in range(0, len(symbols), chunk):
            output.extend(self.quotes(symbols[index : index + chunk], greeks=greeks))
        return output

    def expirations(self, symbol: str) -> list[str]:
        payload = self.request(
            "GET",
            "/markets/options/expirations",
            params={"symbol": symbol, "includeAllRoots": "true", "strikes": "false"},
        )
        dates = payload.get("expirations", {}).get("date") if payload.get("expirations") else None
        if dates is None:
            return []
        return [dates] if isinstance(dates, str) else list(dates)

    def option_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "/markets/options/chains",
            params={"symbol": symbol, "expiration": expiration, "greeks": str(bool(greeks)).lower()},
        )
        option = payload.get("options", {}).get("option") if payload.get("options") else None
        return self._as_list(option)

    def timesales(
        self,
        symbol: str,
        interval: str = "1min",
        start: str | None = None,
        end: str | None = None,
        session_filter: str = "open",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "session_filter": session_filter,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        payload = self.request("GET", "/markets/timesales", params=params)
        data = payload.get("series", {}).get("data") if payload.get("series") else None
        return self._as_list(data)

    def preview_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body["preview"] = "true"
        result = self.request("POST", f"/accounts/{self.account_id}/orders", data=body)
        return result.get("order", result)

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body.pop("preview", None)
        result = self.request("POST", f"/accounts/{self.account_id}/orders", data=body)
        return result.get("order", result)

    def change_order(
        self, order_id: str | int, *, order_type: str, duration: str, price: float
    ) -> dict[str, Any]:
        if order_type not in {"market", "limit", "stop", "stop_limit"}:
            raise TradierError(
                f"Tradier change-order endpoint does not accept type={order_type!r}; cancel/replace is required"
            )
        payload = {"type": order_type, "duration": duration, "price": f"{price:.2f}"}
        result = self.request(
            "PUT", f"/accounts/{self.account_id}/orders/{order_id}", data=payload
        )
        return result.get("order", result)

    def cancel_order(self, order_id: str | int) -> dict[str, Any]:
        return self.request("DELETE", f"/accounts/{self.account_id}/orders/{order_id}")


def normalize_quote(raw: dict[str, Any]) -> dict[str, Any]:
    symbol = str(raw.get("symbol") or "")
    bid = _float(raw.get("bid"))
    ask = _float(raw.get("ask"))
    last = _float(raw.get("last"))
    close = _float(raw.get("close"))
    price = last or ((bid + ask) / 2 if bid is not None and ask is not None else None) or close
    change_pct = _float(raw.get("change_percentage"))
    if change_pct is not None:
        change_pct /= 100.0
    if change_pct is None and price is not None and close not in (None, 0):
        change_pct = price / close - 1.0
    quote_ts = _timestamp_iso(raw.get("trade_date") or raw.get("bid_date") or raw.get("ask_date"))
    return {
        "symbol": symbol,
        "price": price,
        "bid": bid,
        "ask": ask,
        "change_pct": change_pct or 0.0,
        "volume": _float(raw.get("volume")) or 0.0,
        "quote_timestamp": quote_ts,
        "payload": raw,
    }


def normalize_option(raw: dict[str, Any]) -> dict[str, Any]:
    greeks = raw.get("greeks") or {}
    bid = _float(raw.get("bid")) or 0.0
    ask = _float(raw.get("ask")) or 0.0
    last = _float(raw.get("last")) or 0.0
    midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 else last
    iv = _float(greeks.get("mid_iv")) or _float(greeks.get("smv_vol")) or 0.0
    return {
        "symbol": str(raw.get("symbol") or ""),
        "underlying": str(raw.get("root_symbol") or raw.get("underlying") or "SPY"),
        "expiration": str(raw.get("expiration_date") or ""),
        "strike": _float(raw.get("strike")) or 0.0,
        "right": "C" if str(raw.get("option_type", "")).lower().startswith("c") else "P",
        "bid": bid,
        "ask": ask,
        "midpoint": midpoint,
        "last": last,
        "volume": int(_float(raw.get("volume")) or 0),
        "open_interest": int(_float(raw.get("open_interest")) or 0),
        "bid_size": int(_float(raw.get("bidsize")) or 0),
        "ask_size": int(_float(raw.get("asksize")) or 0),
        "iv": iv,
        "delta": _float(greeks.get("delta")),
        "gamma": _float(greeks.get("gamma")),
        "theta": _float(greeks.get("theta")),
        "vega": _float(greeks.get("vega")),
        "payload": raw,
    }


def order_quantities(order: dict[str, Any]) -> tuple[float, float]:
    """Return executed and remaining order quantity using Tradier's canonical fields."""
    executed = _float(order.get("exec_quantity")) or 0.0
    remaining = _float(order.get("remaining_quantity"))
    if remaining is None:
        quantity = _float(order.get("quantity")) or 0.0
        remaining = max(0.0, quantity - executed)
    return float(executed), float(remaining)


def preview_fees(preview: dict[str, Any] | None) -> float | None:
    if not preview:
        return None
    commission = _float(preview.get("commission"))
    fees = _float(preview.get("fees"))
    if commission is None and fees is None:
        return None
    return max(0.0, float(commission or 0.0)) + max(0.0, float(fees or 0.0))


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError, OverflowError):
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            return text or None

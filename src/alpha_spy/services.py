from __future__ import annotations

import contextlib
import json
import math
import random
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from scipy.stats import norm

from .config import SuiteConfig
from .db import Journal
from .execution import ExecutionManager
from .features import compute_features
from .prediction import create_prediction
from .publisher import DashboardPublisher
from .risk import AccountState, choose_decision, parse_account_state
from .state import build_dashboard_state
from .strategy import generate_candidates
from .timeutil import ET, at_or_after_et, et_now, utc_iso, utc_now
from .tradier import TradierClient, TradierError, normalize_option, normalize_quote
from .universe import Holding, UniverseProvider


class StopFlag:
    def __init__(self) -> None:
        self.stopped = False
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

    def _stop(self, *_: Any) -> None:
        self.stopped = True


def sleep_interruptible(flag: StopFlag, seconds: float) -> None:
    end = time.monotonic() + max(0.0, seconds)
    while not flag.stopped and time.monotonic() < end:
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _quote_is_stale(timestamp: str | None, maximum_age_seconds: int) -> bool:
    if not timestamp:
        return True
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age = (utc_now() - parsed.astimezone(UTC)).total_seconds()
        return age > maximum_age_seconds or age < -30
    except ValueError:
        return True


def _select_reference_expiration(
    expirations: list[str], minimum_dte: int, maximum_dte: int
) -> str | None:
    today = et_now().date()
    parsed: list[tuple[int, str]] = []
    for value in expirations:
        try:
            expiry = datetime.fromisoformat(value).date()
        except ValueError:
            continue
        dte = (expiry - today).days
        if dte >= 0:
            parsed.append((dte, value))
    if not parsed:
        return None
    in_window = [item for item in parsed if minimum_dte <= item[0] <= maximum_dte]
    return min(in_window or parsed, key=lambda item: abs(item[0] - minimum_dte))[1]


def _option_surface(options: list[dict[str, Any]], spot: float) -> dict[str, Any] | None:
    valid = [
        row for row in options
        if float(row.get("iv") or 0.0) > 0.0
        and float(row.get("ask") or 0.0) > 0.0
        and float(row.get("strike") or 0.0) > 0.0
    ]
    if not valid:
        return None
    calls = [row for row in valid if row.get("right") == "C"]
    puts = [row for row in valid if row.get("right") == "P"]
    atm_rows = []
    if calls:
        atm_rows.append(min(calls, key=lambda row: abs(float(row["strike"]) - spot)))
    if puts:
        atm_rows.append(min(puts, key=lambda row: abs(float(row["strike"]) - spot)))
    if not atm_rows:
        return None
    atm_iv = float(np.mean([float(row["iv"]) for row in atm_rows]))

    delta_calls = [row for row in calls if row.get("delta") is not None]
    delta_puts = [row for row in puts if row.get("delta") is not None]
    call_25 = min(delta_calls, key=lambda row: abs(float(row["delta"]) - 0.25), default=None)
    put_25 = min(delta_puts, key=lambda row: abs(float(row["delta"]) + 0.25), default=None)
    call_iv = float(call_25["iv"]) if call_25 else None
    put_iv = float(put_25["iv"]) if put_25 else None
    skew = put_iv - call_iv if put_iv is not None and call_iv is not None else None
    return {
        "atm_iv": atm_iv,
        "put_25d_iv": put_iv,
        "call_25d_iv": call_iv,
        "skew": skew,
        "quote_count": len(valid),
    }


class MarketService:
    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal
        self.universe = UniverseProvider(config)
        self._demo_tick = 0

    def run_forever(self) -> None:
        flag = StopFlag()
        while not flag.stopped:
            started = time.perf_counter()
            try:
                snapshot_id = self.run_once()
                latency = (time.perf_counter() - started) * 1000.0
                self.journal.heartbeat("market", "ONLINE", latency, f"snapshot={snapshot_id}")
            except Exception as exc:
                self.journal.heartbeat("market", "ERROR", None, str(exc))
                self.journal.alert("critical", "Market collection failed", str(exc), "market")
            sleep_interruptible(flag, self.config.market.poll_interval_seconds)

    def run_once(self) -> str:
        holdings = self.universe.get()
        if self.config.tradier.access_token.get_secret_value():
            return self._collect_tradier(holdings)
        if not self.config.market.demo_when_unconfigured:
            raise RuntimeError("Tradier is not configured and demo mode is disabled")
        return self._collect_demo(holdings)

    def _collect_tradier(self, holdings: list[Holding]) -> str:
        with TradierClient(self.config) as client:
            clock = client.clock()
            symbols = [h.symbol for h in holdings[: self.config.universe.top_n_quotes]]
            symbols = list(dict.fromkeys(["SPY", *self.config.market.include_symbols, *symbols]))
            raw_quotes = client.quotes_chunked(symbols)
            normalized = {q["symbol"]: q for q in map(normalize_quote, raw_quotes) if q["symbol"]}
            weight_map = {h.symbol: h for h in holdings}
            quotes: list[dict[str, Any]] = []
            covered_weight = 0.0
            for symbol, quote in normalized.items():
                holding = weight_map.get(symbol)
                weight = holding.weight if holding else 0.0
                stale = _quote_is_stale(
                    quote.get("quote_timestamp"),
                    self.config.market.quote_stale_seconds,
                )
                if holding and quote.get("price") is not None and not stale:
                    covered_weight += weight
                quotes.append(
                    {
                        **quote,
                        "weight": weight,
                        "sector": holding.sector if holding else "ETF",
                        "stale": stale,
                    }
                )
            spy = normalized.get("SPY")
            if not spy or not spy.get("price"):
                raise RuntimeError("Tradier did not return a usable SPY quote")
            spy_stale = next((bool(q.get("stale")) for q in quotes if q.get("symbol") == "SPY"), True)
            integrity = "VERIFIED"
            if covered_weight < self.config.universe.minimum_covered_weight or spy_stale:
                integrity = "INCOMPLETE"
            snapshot_id = self._save_snapshot(
                exchange_state=str(clock.get("state", "unknown")),
                source="tradier",
                quotes=quotes,
                covered_weight=covered_weight,
                integrity=integrity,
                extra={"clock": clock, "rate_state": client.rate_state.__dict__},
            )
            self._collect_spy_chain(client, snapshot_id, float(spy["price"]))
            self._collect_constituent_iv_context(
                client, snapshot_id, holdings, normalized
            )
            return snapshot_id

    def _collect_spy_chain(self, client: TradierClient, snapshot_id: str, spy_price: float) -> None:
        expirations = client.expirations("SPY")
        if not expirations:
            self.journal.alert("warning", "No SPY expirations", "Tradier returned no expirations", "market")
            return
        today = et_now().date().isoformat()
        if self.config.market.option_expiration_mode == "zero_dte":
            if today not in expirations:
                self.journal.alert(
                    "warning",
                    "No 0DTE SPY expiry",
                    "Tradier has no same-session expiration; refusing chain fallback",
                    "market",
                )
                return
            expiration = today
        else:
            eligible = [e for e in expirations if e >= today]
            expiration = today if today in eligible else (eligible[0] if eligible else expirations[0])
        options = [normalize_option(row) for row in client.option_chain("SPY", expiration, self.config.market.option_chain_greeks)]
        options = [o for o in options if abs(float(o["strike"]) - spy_price) <= max(30.0, spy_price * 0.06)]
        options = sorted(options, key=lambda o: (o["right"], o["strike"]))[: self.config.market.max_option_chain_rows]
        chain_id = f"OC-{snapshot_id}"
        self.journal.insert_option_chain(
            {
                "chain_snapshot_id": chain_id,
                "captured_at": utc_iso(),
                "underlying": "SPY",
                "purpose": "strategy",
                "expiration": expiration,
                "underlying_price": spy_price,
                "integrity": "VERIFIED" if options else "INCOMPLETE",
                "source": "tradier",
                "payload": {},
            },
            options,
        )
        append_jsonl(
            self.config.paths.state_root / "market" / f"spy-options-{et_now().date().isoformat()}.jsonl",
            {"chain_snapshot_id": chain_id, "captured_at": utc_iso(), "expiration": expiration, "options": options},
        )

    def _collect_constituent_iv_context(
        self,
        client: TradierClient,
        snapshot_id: str,
        holdings: list[Holding],
        quote_map: dict[str, dict[str, Any]],
    ) -> None:
        now = utc_now()
        last_text = self.journal.get_control("last_constituent_iv_collection_at")
        should_collect = True
        if last_text:
            try:
                last = datetime.fromisoformat(last_text.replace("Z", "+00:00"))
                should_collect = (now - last.astimezone(UTC)).total_seconds() >= (
                    self.config.market.constituent_iv_refresh_seconds
                )
            except ValueError:
                should_collect = True

        top = holdings[: self.config.universe.top_n_option_iv]
        reference_expiration: str | None = None
        spy_surface: dict[str, Any] | None = None

        if should_collect:
            try:
                spy_expirations = client.expirations("SPY")
                reference_expiration = _select_reference_expiration(
                    spy_expirations,
                    self.config.market.iv_reference_min_dte,
                    self.config.market.iv_reference_max_dte,
                )
                if reference_expiration:
                    spy_options = [
                        normalize_option(row)
                        for row in client.option_chain("SPY", reference_expiration, True)
                    ]
                    spy_price = float(quote_map.get("SPY", {}).get("price") or 0.0)
                    spy_surface = _option_surface(spy_options, spy_price)
                    if spy_surface:
                        self.journal.insert_option_chain(
                            {
                                "chain_snapshot_id": f"OCR-{snapshot_id}",
                                "captured_at": utc_iso(now),
                                "underlying": "SPY",
                                "purpose": "iv_reference",
                                "expiration": reference_expiration,
                                "underlying_price": spy_price,
                                "integrity": "VERIFIED",
                                "source": "tradier",
                                "payload": {"surface": spy_surface},
                            },
                            spy_options[: self.config.market.max_option_chain_rows],
                        )
            except Exception as exc:
                self.journal.alert(
                    "warning", "SPY reference surface unavailable", str(exc), "market"
                )

            cursor = int(self.journal.get_control("constituent_iv_cursor", "0") or 0)
            batch_size = max(1, self.config.market.constituent_option_batch_size)
            if top:
                indexes = [(cursor + offset) % len(top) for offset in range(min(batch_size, len(top)))]
                batch = [top[index] for index in indexes]
            else:
                batch = []
            observations: list[dict[str, Any]] = []
            failures: list[str] = []
            for holding in batch:
                quote = quote_map.get(holding.symbol)
                spot = float((quote or {}).get("price") or 0.0)
                if spot <= 0:
                    failures.append(f"{holding.symbol}:missing_spot")
                    continue
                try:
                    expirations = client.expirations(holding.symbol)
                    expiration = (
                        reference_expiration
                        if reference_expiration in expirations
                        else _select_reference_expiration(
                            expirations,
                            self.config.market.iv_reference_min_dte,
                            self.config.market.iv_reference_max_dte,
                        )
                    )
                    if not expiration:
                        failures.append(f"{holding.symbol}:no_expiration")
                        continue
                    rows = [
                        normalize_option(row)
                        for row in client.option_chain(holding.symbol, expiration, True)
                    ]
                    surface = _option_surface(rows, spot)
                    if not surface:
                        failures.append(f"{holding.symbol}:no_surface")
                        continue
                    dte = max(0, (datetime.fromisoformat(expiration).date() - et_now().date()).days)
                    observations.append(
                        {
                            "observation_id": f"IV-{snapshot_id}-{holding.symbol}",
                            "captured_at": utc_iso(now),
                            "snapshot_id": snapshot_id,
                            "symbol": holding.symbol,
                            "weight": holding.weight,
                            "expiration": expiration,
                            "dte": dte,
                            "underlying_price": spot,
                            **surface,
                            "integrity": "VERIFIED",
                            "payload": {},
                        }
                    )
                except Exception as exc:
                    failures.append(f"{holding.symbol}:{type(exc).__name__}")
            self.journal.insert_constituent_iv(observations)
            self.journal.set_control(
                "constituent_iv_cursor", str((cursor + len(batch)) % max(len(top), 1))
            )
            self.journal.set_control("last_constituent_iv_collection_at", utc_iso(now))
            if failures:
                self.journal.alert(
                    "warning",
                    "Partial constituent IV collection",
                    ", ".join(failures[:12]),
                    "market",
                    {"failure_count": len(failures)},
                )

        reference_chain, reference_options = self.journal.latest_option_chain(
            "SPY", "iv_reference"
        )
        if reference_chain and reference_options:
            reference_expiration = reference_chain.get("expiration")
            spy_surface = _option_surface(
                reference_options, float(reference_chain.get("underlying_price") or 0.0)
            )

        not_before = utc_iso(
            now - timedelta(minutes=self.config.market.constituent_iv_max_age_minutes)
        )
        observations = self.journal.latest_constituent_iv(
            [holding.symbol for holding in top], not_before
        )
        top_weight = sum(holding.weight for holding in top) or 1.0
        observed_weight = sum(float(row.get("weight") or 0.0) for row in observations)
        constituent_iv = None
        constituent_skew = None
        if observed_weight > 0:
            constituent_iv = sum(
                float(row["weight"]) * float(row["atm_iv"]) for row in observations
            ) / observed_weight
            skew_rows = [row for row in observations if row.get("skew") is not None]
            skew_weight = sum(float(row["weight"]) for row in skew_rows)
            if skew_weight > 0:
                constituent_skew = sum(
                    float(row["weight"]) * float(row["skew"]) for row in skew_rows
                ) / skew_weight
        spy_iv = float(spy_surface["atm_iv"]) if spy_surface else None
        spy_skew = float(spy_surface["skew"]) if spy_surface and spy_surface.get("skew") is not None else None
        covered_weight = min(1.0, observed_weight / top_weight)
        metrics = {
            "snapshot_id": snapshot_id,
            "created_at": utc_iso(now),
            "reference_expiration": reference_expiration,
            "spy_iv": spy_iv,
            "constituent_iv": constituent_iv,
            "vol_gap": (spy_iv - constituent_iv) if spy_iv is not None and constituent_iv is not None else None,
            "spy_skew": spy_skew,
            "constituent_skew": constituent_skew,
            "skew_gap": (spy_skew - constituent_skew) if spy_skew is not None and constituent_skew is not None else None,
            "covered_weight": covered_weight,
            "symbol_count": len(observations),
            "integrity": "VERIFIED" if covered_weight >= 0.70 and spy_iv is not None else "INCOMPLETE",
            "payload": {
                "top_universe_weight": top_weight,
                "observed_weight": observed_weight,
                "constituent_expirations": sorted({row["expiration"] for row in observations}),
            },
        }
        self.journal.insert_surface_metrics(metrics)
        append_jsonl(
            self.config.paths.state_root / "market" / f"iv-surfaces-{et_now().date().isoformat()}.jsonl",
            metrics,
        )

    def _collect_demo(self, holdings: list[Holding]) -> str:
        self._demo_tick += 1
        rng = random.Random(20260806 + self._demo_tick)
        phase = self._demo_tick / 9.0
        spy_price = 635.0 + 0.65 * math.sin(phase / 2.0) + 0.18 * math.sin(phase * 1.7)
        quotes: list[dict[str, Any]] = [
            {
                "symbol": "SPY",
                "price": spy_price,
                "bid": spy_price - 0.01,
                "ask": spy_price + 0.01,
                "change_pct": 0.0015 + 0.0008 * math.sin(phase / 3.0),
                "volume": 50_000_000,
                "quote_timestamp": utc_iso(),
                "weight": 0.0,
                "sector": "ETF",
                "stale": False,
                "payload": {"demo": True},
            }
        ]
        selected = holdings[: max(20, min(len(holdings), self.config.universe.top_n_quotes))]
        total = sum(h.weight for h in selected) or 1.0
        for index, holding in enumerate(selected):
            beta_wave = 0.0012 * math.sin(phase + index / 6.0)
            idio = rng.gauss(0.0, 0.004)
            change_pct = 0.65 * quotes[0]["change_pct"] + beta_wave + idio
            base = 40.0 + (index + 1) * 4.7
            price = max(2.0, base * (1.0 + change_pct))
            quotes.append(
                {
                    "symbol": holding.symbol,
                    "price": price,
                    "bid": max(0.01, price - 0.01),
                    "ask": price + 0.01,
                    "change_pct": change_pct,
                    "volume": int(500_000 + rng.random() * 10_000_000),
                    "quote_timestamp": utc_iso(),
                    "weight": holding.weight / total,
                    "sector": holding.sector,
                    "stale": False,
                    "payload": {"demo": True},
                }
            )
        covered_weight = min(1.0, sum(float(q["weight"]) for q in quotes if q["symbol"] != "SPY"))
        snapshot_id = self._save_snapshot(
            exchange_state="open",
            source="demo",
            quotes=quotes,
            covered_weight=covered_weight,
            integrity="VERIFIED",
            extra={"demo": True, "selected_weight_normalized": total},
        )
        self._save_demo_chain(snapshot_id, spy_price)
        demo_constituent_iv = 0.175 + 0.006 * math.sin(phase / 2.5)
        demo_spy_iv = demo_constituent_iv + 0.012 + 0.003 * math.sin(phase)
        demo_constituent_skew = 0.021 + 0.002 * math.sin(phase / 3.0)
        demo_spy_skew = demo_constituent_skew + 0.006
        self.journal.insert_surface_metrics(
            {
                "snapshot_id": snapshot_id,
                "created_at": utc_iso(),
                "reference_expiration": (et_now().date() + timedelta(days=7)).isoformat(),
                "spy_iv": demo_spy_iv,
                "constituent_iv": demo_constituent_iv,
                "vol_gap": demo_spy_iv - demo_constituent_iv,
                "spy_skew": demo_spy_skew,
                "constituent_skew": demo_constituent_skew,
                "skew_gap": demo_spy_skew - demo_constituent_skew,
                "covered_weight": 1.0,
                "symbol_count": len(selected),
                "integrity": "VERIFIED",
                "payload": {"demo": True},
            }
        )
        return snapshot_id

    def _save_demo_chain(self, snapshot_id: str, spy_price: float) -> None:
        expiration = et_now().date().isoformat()
        t = max(30.0 / (365 * 24 * 60), 1e-6)
        vol = 0.19
        strikes = np.arange(math.floor(spy_price - 15), math.ceil(spy_price + 15) + 1, 1.0)
        options: list[dict[str, Any]] = []
        for right in ("C", "P"):
            for strike in strikes:
                d1 = (math.log(spy_price / strike) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
                d2 = d1 - vol * math.sqrt(t)
                if right == "C":
                    theo = spy_price * norm.cdf(d1) - strike * norm.cdf(d2)
                    delta = norm.cdf(d1)
                else:
                    theo = strike * norm.cdf(-d2) - spy_price * norm.cdf(-d1)
                    delta = norm.cdf(d1) - 1.0
                theo = max(0.03, theo)
                spread = max(0.02, min(0.18, 0.06 * theo + 0.02))
                bid = max(0.01, theo - spread / 2)
                ask = theo + spread / 2
                occ = f"SPY{expiration[2:4]}{expiration[5:7]}{expiration[8:10]}{right}{int(strike*1000):08d}"
                options.append(
                    {
                        "symbol": occ,
                        "expiration": expiration,
                        "strike": float(strike),
                        "right": right,
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "midpoint": round((bid + ask) / 2, 2),
                        "volume": 500,
                        "open_interest": 2000,
                        "iv": vol,
                        "delta": float(delta),
                        "gamma": 0.02,
                        "theta": -0.05,
                        "vega": 0.08,
                        "payload": {"demo": True},
                    }
                )
        self.journal.insert_option_chain(
            {
                "chain_snapshot_id": f"OC-{snapshot_id}",
                "captured_at": utc_iso(),
                "underlying": "SPY",
                "expiration": expiration,
                "underlying_price": spy_price,
                "integrity": "VERIFIED",
                "source": "demo",
                "payload": {"demo": True},
            },
            options,
        )

    def _save_snapshot(
        self,
        *,
        exchange_state: str,
        source: str,
        quotes: list[dict[str, Any]],
        covered_weight: float,
        integrity: str,
        extra: dict[str, Any],
    ) -> str:
        now = utc_now()
        snapshot_id = f"S-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        spy = next((q for q in quotes if q.get("symbol") == "SPY"), {})
        stale_count = sum(bool(q.get("stale")) for q in quotes)
        snapshot = {
            "snapshot_id": snapshot_id,
            "captured_at": utc_iso(now),
            "exchange_state": exchange_state,
            "spy_price": spy.get("price"),
            "spy_bid": spy.get("bid"),
            "spy_ask": spy.get("ask"),
            "covered_weight": covered_weight,
            "quote_count": len(quotes),
            "stale_quote_count": stale_count,
            "integrity": integrity,
            "source": source,
            "payload": extra,
        }
        persist_full = str(exchange_state).lower() in {"open", "pre"}
        stored_quotes = quotes if persist_full else [q for q in quotes if q.get("symbol") == "SPY"]
        snapshot["quote_count"] = len(stored_quotes)
        snapshot["stale_quote_count"] = sum(bool(q.get("stale")) for q in stored_quotes)
        self.journal.insert_snapshot(snapshot, stored_quotes)
        # Full-universe JSONL is ~14 MB per snapshot. Writing it around the
        # clock filled the disk over a weekend (20 GB on Sunday alone) and
        # took every service down. Persist the 500-name tape only while the
        # cash session is open or in pre-market.
        if persist_full:
            append_jsonl(
                self.config.paths.state_root / "market" / f"quotes-{et_now().date().isoformat()}.jsonl",
                {"snapshot": snapshot, "quotes": quotes},
            )
        return snapshot_id


class EngineService:
    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal
        self.publisher = DashboardPublisher(config)
        self.execution = ExecutionManager(config, journal)

    def run_forever(self) -> None:
        flag = StopFlag()
        while not flag.stopped:
            started = time.perf_counter()
            try:
                processed = self.run_once()
                latency = (time.perf_counter() - started) * 1000.0
                self.journal.heartbeat("engine", "ONLINE", latency, processed or "idle")
            except Exception as exc:
                self.journal.heartbeat("engine", "ERROR", None, str(exc))
                self.journal.alert("critical", "Engine cycle failed", str(exc), "engine")
                # Best effort: the journal already holds the alert, so a
                # dashboard publish failure must not mask the engine error.
                with contextlib.suppress(Exception):
                    self.publisher.alert("critical", "Engine cycle failed", str(exc), "engine")
            sleep_interruptible(flag, 5.0)

    def run_once(self) -> str | None:
        # Supervisory commands are applied before a new decision so pause and
        # flatten requests do not wait for a successful market-processing cycle.
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
        self.journal.insert_prediction(prediction)
        chain, options = self.journal.latest_option_chain("SPY")
        if chain and chain["captured_at"] < snapshot["captured_at"]:
            options = []
        candidates = generate_candidates(self.config, prediction, options)
        self.journal.insert_candidates(candidates)
        account = self._account_state()
        decision = choose_decision(self.config, self.journal, prediction, feature, candidates, account)
        self.journal.insert_decision(decision)
        if decision["action"] in {"PAPER_ORDER", "SUBMIT_ORDER"} and decision.get("candidate_id"):
            candidate = next((c for c in candidates if c["candidate_id"] == decision["candidate_id"]), None)
            if candidate:
                try:
                    self.execution.execute(decision, candidate)
                except Exception as exc:
                    self.journal.alert("critical", "Order execution failed", str(exc), "execution")
                    self.publisher.alert("critical", "Order execution failed", str(exc), "execution")
        self.journal.set_control("last_engine_snapshot_id", snapshot["snapshot_id"])
        self._publish(snapshot, feature, prediction, account)
        append_jsonl(
            self.config.paths.state_root / "candidates" / f"candidates-{et_now().date().isoformat()}.jsonl",
            {"prediction": prediction, "feature": feature, "candidates": candidates, "decision": decision},
        )
        return f"prediction={prediction['prediction_id']} action={decision['action']}"

    def _account_state(self) -> AccountState:
        session_date = et_now().date().isoformat()
        managed_pnl = self.journal.managed_daily_pnl(session_date)
        if not self.config.tradier.access_token.get_secret_value() or not self.config.tradier.account_id:
            return AccountState(25_000.0, 25_000.0, 25_000.0, managed_pnl)
        try:
            with TradierClient(self.config) as client:
                broker = parse_account_state(client.balances())
                return AccountState(
                    equity=broker.equity,
                    cash=broker.cash,
                    buying_power=broker.buying_power,
                    daily_pnl=managed_pnl,
                )
        except Exception as exc:
            self.journal.alert("warning", "Account poll failed", str(exc), "engine")
            return AccountState(25_000.0, 25_000.0, 0.0, managed_pnl)

    def _publish(
        self,
        snapshot: dict[str, Any],
        feature: dict[str, Any],
        prediction: dict[str, Any],
        account: AccountState,
    ) -> None:
        state = build_dashboard_state(self.config, self.journal, account)
        try:
            self.publisher.snapshot(state, snapshot["captured_at"])
            self.publisher.prediction(
                {
                    "prediction_id": prediction["prediction_id"],
                    "created_at": prediction["created_at"],
                    "target_at": prediction["target_at"],
                    "horizon_minutes": prediction["horizon_minutes"],
                    "spy_price": prediction["spy_price"],
                    "predicted_price": prediction["predicted_price"],
                    "predicted_low": prediction["predicted_low"],
                    "predicted_high": prediction["predicted_high"],
                    "probability_up": prediction["probability_up"],
                    "integrity": "PENDING",
                    "model_version": prediction["model_version"],
                    "payload": prediction.get("payload", {}),
                }
            )
        except Exception as exc:
            self.journal.heartbeat("dashboard-publisher", "DEGRADED", None, str(exc))

    def _process_commands(self) -> None:
        try:
            commands = self.publisher.pending_commands()
        except Exception:
            return
        for command in commands:
            command_id = int(command["id"])
            name = command["command"]
            try:
                self.publisher.command_status(command_id, "acknowledged", "engine accepted command")
                if name == "PAUSE_NEW_ENTRIES":
                    self.journal.set_control("entries_paused", "true")
                elif name == "RESUME_NEW_ENTRIES":
                    self.journal.set_control("entries_paused", "false")
                elif name == "RELOAD_MODEL":
                    self.journal.set_control("reload_model_requested", utc_iso())
                elif name == "FLATTEN_MANAGED_POSITION":
                    self.journal.set_control("flatten_requested", "true")
                self.publisher.command_status(command_id, "completed", f"{name} applied")
            except Exception as exc:
                # Best effort: reporting the failure must not itself raise.
                with contextlib.suppress(Exception):
                    self.publisher.command_status(command_id, "failed", str(exc))


class ConfirmationService:
    INTEGRITY_RANK: ClassVar[dict[str, int]] = {
        "VERIFIED": 0,
        "MINOR_REVISION": 1,
        "MATERIAL_REVISION": 2,
        "INCOMPLETE": 3,
        "INVALID": 4,
    }

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal
        self.publisher = DashboardPublisher(config)

    def run_forever(self) -> None:
        flag = StopFlag()
        while not flag.stopped:
            started = time.perf_counter()
            try:
                count = self.run_once()
                latency = (time.perf_counter() - started) * 1000.0
                self.journal.heartbeat("confirmation", "ONLINE", latency, f"matured={count}")
            except Exception as exc:
                self.journal.heartbeat("confirmation", "ERROR", None, str(exc))
                self.journal.alert("critical", "Confirmation cycle failed", str(exc), "confirmation")
            sleep_interruptible(flag, self.config.audit.confirmation_interval_seconds)

    def _combine_integrity(self, *values: str) -> str:
        return max(values, key=lambda value: self.INTEGRITY_RANK.get(value, 4))

    def _revision_check(self, prediction: dict[str, Any]) -> dict[str, Any]:
        frozen = float(prediction["spy_price"])
        with self.journal.connect() as con:
            source_row = con.execute(
                "SELECT source FROM market_snapshots WHERE snapshot_id=?",
                (prediction["snapshot_id"],),
            ).fetchone()
        source = str(source_row[0]) if source_row else "unknown"
        check = {
            "prediction_id": prediction["prediction_id"],
            "checked_at": utc_iso(),
            "frozen_price": frozen,
            "historical_price": None,
            "revision_bps": None,
            "status": "INCOMPLETE",
            "source": source,
            "payload": {},
        }
        if source == "demo":
            check.update(
                historical_price=frozen,
                revision_bps=0.0,
                status="VERIFIED",
                payload={"reason": "deterministic_demo_tape"},
            )
            self.journal.insert_revision_check(check)
            return check
        if not self.config.tradier.market_access_token.get_secret_value():
            check["payload"] = {"reason": "tradier_market_data_not_configured"}
            self.journal.insert_revision_check(check)
            return check

        created = datetime.fromisoformat(str(prediction["created_at"]).replace("Z", "+00:00"))
        local = created.astimezone(ET)
        start = (local - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
        end = (local + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")
        try:
            with TradierClient(self.config, purpose="market") as client:
                bars = client.timesales(
                    "SPY", interval="1min", start=start, end=end, session_filter="all"
                )
            candidates: list[tuple[float, dict[str, Any]]] = []
            for bar in bars:
                timestamp = bar.get("timestamp") or bar.get("time") or bar.get("date")
                if timestamp is None:
                    continue
                try:
                    numeric_timestamp = float(timestamp)
                    if numeric_timestamp > 10_000_000_000:
                        numeric_timestamp /= 1000.0
                    bar_time = datetime.fromtimestamp(numeric_timestamp, UTC)
                    delta = abs((bar_time - created.astimezone(UTC)).total_seconds())
                except (TypeError, ValueError, OSError):
                    try:
                        bar_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                        if bar_time.tzinfo is None:
                            bar_time = bar_time.replace(tzinfo=ET)
                        delta = abs((bar_time.astimezone(UTC) - created.astimezone(UTC)).total_seconds())
                    except ValueError:
                        continue
                candidates.append((delta, bar))
            if not candidates:
                check["payload"] = {"reason": "historical_bar_missing", "start": start, "end": end}
            else:
                _, bar = min(candidates, key=lambda item: item[0])
                historical = _safe_float(
                    bar.get("price") or bar.get("close") or bar.get("open"), 0.0
                )
                if historical <= 0:
                    check["payload"] = {"reason": "historical_price_invalid", "bar": bar}
                else:
                    revision_bps = abs(historical - frozen) / max(frozen, 1e-9) * 10_000.0
                    threshold = self.config.audit.maximum_snapshot_revision_bps
                    status = (
                        "VERIFIED"
                        if revision_bps <= threshold
                        else "MINOR_REVISION"
                        if revision_bps <= threshold * 5.0
                        else "MATERIAL_REVISION"
                    )
                    check.update(
                        historical_price=historical,
                        revision_bps=revision_bps,
                        status=status,
                        payload={"bar": bar, "start": start, "end": end},
                    )
        except Exception as exc:
            check["payload"] = {"reason": "historical_api_error", "error": str(exc)}
        self.journal.insert_revision_check(check)
        return check

    def _score_candidates(self, prediction: dict[str, Any]) -> None:
        chain, options = self.journal.option_chain_at_or_after(
            prediction["target_at"],
            purpose="strategy",
            tolerance_seconds=max(180, self.config.market.poll_interval_seconds * 3),
        )
        if not chain or not options:
            return
        option_map = {row["symbol"]: row for row in options}
        for candidate in self.journal.candidates_for_prediction(prediction["prediction_id"]):
            legs = candidate.get("legs", [])
            if not legs or any(leg["symbol"] not in option_map for leg in legs):
                continue
            current_net = 0.0
            for leg in legs:
                quote = option_map[leg["symbol"]]
                quantity = int(leg.get("quantity", 1))
                if str(leg["side"]).startswith("buy"):
                    liquidation = float(quote.get("bid") or quote.get("midpoint") or 0.0)
                    current_net += liquidation * quantity
                else:
                    cover = float(quote.get("ask") or quote.get("midpoint") or 0.0)
                    current_net -= cover * quantity
            mark_value = abs(current_net)
            entry_kind = str(candidate.get("payload", {}).get("entry_kind") or "debit")
            entry = float(candidate["entry_price"])
            pnl = (
                (mark_value - entry) * 100.0
                if entry_kind == "debit"
                else (entry - mark_value) * 100.0
            )
            self.journal.insert_candidate_outcome(
                {
                    "candidate_id": candidate["candidate_id"],
                    "confirmed_at": utc_iso(),
                    "mark_value": mark_value,
                    "pnl": pnl,
                    "mfe": max(0.0, pnl),
                    "mae": min(0.0, pnl),
                    "target_hit": pnl >= 0.50 * float(candidate["max_profit"]),
                    "stop_hit": pnl <= -0.40 * float(candidate["max_loss"]),
                    "payload": {
                        "chain_snapshot_id": chain["chain_snapshot_id"],
                        "chain_delta_seconds": chain.get("delta_seconds"),
                        "entry_kind": entry_kind,
                    },
                }
            )

    def run_once(self) -> int:
        matured = 0
        maturity_cutoff = utc_iso(
            utc_now() - timedelta(seconds=max(0, self.config.audit.maturity_grace_seconds))
        )
        pending = self.journal.pending_predictions(maturity_cutoff)
        for prediction in pending:
            horizon_minutes = int(prediction.get("horizon_minutes") or 15)
            tolerance = max(120, self.config.market.poll_interval_seconds * 2)
            if horizon_minutes >= 390:
                # Daily/research horizons can span a market holiday or a brief collector restart.
                # The actual delta is still persisted and downgraded below; this only prevents
                # an otherwise valid long-horizon forecast from remaining pending forever.
                tolerance = max(tolerance, 36 * 60 * 60)
            realized = self.journal.snapshot_at_or_after(
                prediction["target_at"],
                tolerance_seconds=tolerance,
            )
            if not realized or not realized.get("spy_price"):
                continue
            actual = float(realized["spy_price"])
            initial = float(prediction["spy_price"])
            predicted = float(prediction["predicted_price"])
            actual_return = actual / initial - 1.0
            predicted_up = predicted >= initial
            actual_up = actual >= initial
            brier = (float(prediction["probability_up"]) - (1.0 if actual_up else 0.0)) ** 2
            delta_seconds = float(realized.get("delta_seconds") or 0.0)
            timing_integrity = (
                "VERIFIED"
                if delta_seconds <= self.config.market.poll_interval_seconds * 1.5
                else "MINOR_REVISION"
            )
            revision = self._revision_check(prediction)
            integrity = self._combine_integrity(timing_integrity, revision["status"])
            with self.journal.connect() as con:
                range_row = con.execute(
                    """
                    SELECT MIN(spy_price) AS low, MAX(spy_price) AS high
                    FROM market_snapshots
                    WHERE captured_at >= ? AND captured_at <= ? AND spy_price IS NOT NULL
                    """,
                    (prediction["created_at"], prediction["target_at"]),
                ).fetchone()
            outcome = {
                "prediction_id": prediction["prediction_id"],
                "confirmed_at": utc_iso(),
                "actual_price": actual,
                "actual_high": float(range_row["high"]) if range_row and range_row["high"] is not None else actual,
                "actual_low": float(range_row["low"]) if range_row and range_row["low"] is not None else actual,
                "actual_return": actual_return,
                "direction_correct": predicted_up == actual_up,
                "price_error": actual - predicted,
                "interval_covered": float(prediction["predicted_low"]) <= actual <= float(prediction["predicted_high"]),
                "brier": brier,
                "integrity": integrity,
                "payload": {
                    "realized_snapshot_id": realized["snapshot_id"],
                    "delta_seconds": delta_seconds,
                    "revision_check": revision,
                    "formal_anchor": bool(prediction.get("formal_anchor")),
                },
            }
            self.journal.mature_prediction(outcome)
            self._score_candidates(prediction)
            # Best effort: the outcome is already committed to the immutable
            # tape above, so a dashboard publish failure must not abort
            # maturation or leave the prediction pending.
            with contextlib.suppress(Exception):
                self.publisher.outcome(
                    {
                        "prediction_id": prediction["prediction_id"],
                        "confirmed_at": outcome["confirmed_at"],
                        "actual_price": actual,
                        "actual_high": outcome["actual_high"],
                        "actual_low": outcome["actual_low"],
                        "actual_return": actual_return,
                        "integrity": integrity,
                        "payload": outcome["payload"],
                    }
                )
            matured += 1
        return matured


class SettlementService:
    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal

    def run_forever(self) -> None:
        flag = StopFlag()
        while not flag.stopped:
            started = time.perf_counter()
            try:
                status = self.run_once()
                latency = (time.perf_counter() - started) * 1000.0
                self.journal.heartbeat("settlement", "ONLINE", latency, status)
            except Exception as exc:
                self.journal.heartbeat("settlement", "ERROR", None, str(exc))
                self.journal.alert("critical", "Settlement cycle failed", str(exc), "settlement")
                position = self.journal.open_position()
                if position and at_or_after_et(utc_now(), self.config.risk.forced_flat_time_et):
                    position.update(
                        {
                            "status": "CLOSED",
                            "closed_at": utc_iso(),
                            "exit_reason": "fail_safe_flatten",
                            "unrealized_pnl": 0.0,
                            "realized_pnl": float(position.get("unrealized_pnl") or 0.0),
                        }
                    )
                    self.journal.upsert_position(position)
            sleep_interruptible(flag, 15.0)

    def run_once(self) -> str:
        position = self.journal.open_position()
        if not position:
            return "no_position"
        _, options = self.journal.latest_option_chain("SPY")
        quotes = {o["symbol"]: o for o in options}
        missing = [leg["symbol"] for leg in position["legs"] if leg["symbol"] not in quotes]
        if missing:
            return f"missing_marks={len(missing)}"
        # Mark at executable closing-side prices, not midpoint: long legs liquidate
        # at bid and short legs must be covered at ask.
        current_net = 0.0
        for leg in position["legs"]:
            quote = quotes[leg["symbol"]]
            quantity = int(leg.get("quantity", 1))
            if leg["side"].startswith("buy"):
                liquidation = float(quote.get("bid") or quote.get("midpoint") or 0.0)
                current_net += liquidation * quantity
            else:
                cover = float(quote.get("ask") or quote.get("midpoint") or 0.0)
                current_net -= cover * quantity
        entry_kind = position.get("payload", {}).get("entry_kind", "debit")
        current_value = abs(current_net)
        if entry_kind == "debit":
            pnl = (current_value - float(position["entry_value"])) * 100 * int(position["quantity"])
        else:
            pnl = (float(position["entry_value"]) - current_value) * 100 * int(position["quantity"])
        mfe = max(float(position.get("mfe") or 0.0), pnl)
        mae = min(float(position.get("mae") or 0.0), pnl)
        close_reason = None
        if pnl >= 0.50 * float(position["max_profit"]):
            close_reason = "profit_target"
        elif pnl <= -0.40 * float(position["max_loss"]):
            close_reason = "risk_stop"
        elif self.journal.get_control("flatten_requested", "false").lower() == "true":
            close_reason = "operator_flatten"
            self.journal.set_control("flatten_requested", "false")
        elif at_or_after_et(utc_now(), self.config.risk.forced_flat_time_et):
            close_reason = "forced_flat_time"
        position.update(
            {
                "current_value": current_value,
                "unrealized_pnl": pnl,
                "mfe": mfe,
                "mae": mae,
            }
        )
        if close_reason:
            # In paper mode, settle locally. Live closing is intentionally fail-closed unless
            # the production sentinel and submission configuration are active.
            if not self.config.trading.paper_mode and self.config.trading.submit_orders:
                self.config.assert_live_trading_safe()
                self._close_live(position, current_value)
            position.update(
                {
                    "status": "CLOSED",
                    "closed_at": utc_iso(),
                    "realized_pnl": pnl,
                    "unrealized_pnl": 0.0,
                    "exit_reason": close_reason,
                }
            )
        self.journal.upsert_position(position)
        return close_reason or f"pnl={pnl:.2f}"

    def _close_live(self, position: dict[str, Any], price: float) -> None:
        reverse = {
            "buy_to_open": "sell_to_close",
            "sell_to_open": "buy_to_close",
            "buy_to_close": "sell_to_open",
            "sell_to_close": "buy_to_open",
        }
        legs = [{**leg, "side": reverse[leg["side"]]} for leg in position["legs"]]
        closing_kind = (
            "credit"
            if position.get("payload", {}).get("entry_kind", "debit") == "debit"
            else "debit"
        )
        payload: dict[str, Any] = {
            "class": "option" if len(legs) == 1 else "multileg",
            "symbol": "SPY",
            "duration": "day",
            "type": "limit" if len(legs) == 1 else closing_kind,
            "price": f"{price:.2f}",
            "tag": f"{self.config.trading.tag_prefix}-EXIT-{position['position_id'][-6:]}",
        }
        if len(legs) == 1:
            payload.update(
                {
                    "option_symbol": legs[0]["symbol"],
                    "side": legs[0]["side"],
                    "quantity": int(position["quantity"]) * int(legs[0].get("quantity", 1)),
                }
            )
        else:
            for index, leg in enumerate(legs):
                payload[f"option_symbol[{index}]"] = leg["symbol"]
                payload[f"side[{index}]"] = leg["side"]
                payload[f"quantity[{index}]"] = int(position["quantity"]) * int(leg.get("quantity", 1))

        local_order_id = f"O-{uuid.uuid4().hex[:16]}"
        status = "UNKNOWN"
        broker_id: str | int | None = None
        preview: dict[str, Any] | None = None
        order_state: dict[str, Any] = {}
        with TradierClient(self.config) as client:
            preview = client.preview_order(payload)
            if not ExecutionManager._preview_ok(preview):
                raise TradierError(f"Exit preview failed: {preview}")
            response = client.place_order(payload)
            broker_id = response.get("id")
            if not broker_id:
                raise TradierError(f"Exit order did not return an id: {response}")
            deadline = time.monotonic() + max(5, self.config.trading.limit_price_wait_seconds * 2)
            while time.monotonic() < deadline:
                time.sleep(2)
                order_state = client.order(broker_id)
                status = ExecutionManager._status(order_state)
                if status in ExecutionManager.TERMINAL:
                    break
            if status != "FILLED":
                if status not in ExecutionManager.TERMINAL:
                    client.cancel_order(broker_id)
                    cancel_deadline = time.monotonic() + max(3, self.config.trading.cancel_confirm_seconds)
                    while time.monotonic() < cancel_deadline:
                        time.sleep(1)
                        order_state = client.order(broker_id)
                        status = ExecutionManager._status(order_state)
                        if status in ExecutionManager.TERMINAL:
                            break
                raise TradierError(
                    f"Exit order {broker_id} was not filled; terminal status={status}"
                )

        self.journal.insert_order(
            {
                "local_order_id": local_order_id,
                "decision_id": position.get("decision_id"),
                "broker_order_id": str(broker_id),
                "created_at": utc_iso(),
                "updated_at": utc_iso(),
                "environment": self.config.tradier.environment,
                "status": status,
                "order_class": payload["class"],
                "order_type": payload["type"],
                "requested_price": price,
                "average_fill_price": ExecutionManager._average_fill(order_state),
                "quantity": int(position["quantity"]),
                "previewed": True,
                "payload": {
                    "exit": True,
                    "position_id": position["position_id"],
                    "request": payload,
                    "preview": preview,
                    "response": order_state,
                },
            }
        )



class DojoService:
    """After-hours governance report.

    The dojo never changes the live model or trading configuration. It summarizes
    point-in-time evidence and emits a manual promotion recommendation only.
    """

    def __init__(self, config: SuiteConfig, journal: Journal):
        self.config = config
        self.journal = journal

    @staticmethod
    def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "sample_size": 0,
                "direction_accuracy": None,
                "brier": None,
                "range_coverage": None,
                "price_mae": None,
                "integrity_verified_pct": None,
            }
        n = len(rows)
        return {
            "sample_size": n,
            "direction_accuracy": sum(int(row["direction_correct"]) for row in rows) / n,
            "brier": sum(float(row["brier"]) for row in rows) / n,
            "range_coverage": sum(int(row["interval_covered"]) for row in rows) / n,
            "price_mae": sum(abs(float(row["price_error"])) for row in rows) / n,
            "integrity_verified_pct": sum(row["integrity"] == "VERIFIED" for row in rows) / n,
        }

    def run_once(self, recent_days: int = 20) -> Path:
        now = et_now()
        recent_days = max(1, int(recent_days))
        cutoff = (utc_now() - timedelta(days=recent_days)).isoformat().replace("+00:00", "Z")
        output_dir = self.config.paths.report_dir / "dojo" / now.strftime("%Y%m%dT%H%M%S")
        output_dir.mkdir(parents=True, exist_ok=True)

        with self.journal.connect() as con:
            formal_rows = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT o.*
                    FROM prediction_outcomes o
                    JOIN predictions p ON p.prediction_id=o.prediction_id
                    WHERE p.formal_anchor=1 AND o.confirmed_at>=?
                    ORDER BY o.confirmed_at DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            all_rows = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT * FROM prediction_outcomes
                    WHERE confirmed_at>=?
                    ORDER BY confirmed_at DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            strategies = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT c.strategy,
                           COUNT(*) AS candidates,
                           AVG(c.expected_value) AS avg_expected_value,
                           AVG(c.probability_profit) AS avg_probability,
                           MAX(c.score) AS best_score,
                           SUM(CASE WHEN co.candidate_id IS NOT NULL THEN 1 ELSE 0 END) AS matured,
                           AVG(co.pnl) AS avg_realized_pnl,
                           AVG(CASE WHEN co.pnl > 0 THEN 1.0 ELSE 0.0 END) AS realized_win_rate
                    FROM candidates c
                    LEFT JOIN candidate_outcomes co ON co.candidate_id=c.candidate_id
                    WHERE c.created_at>=?
                    GROUP BY c.strategy
                    ORDER BY COALESCE(AVG(co.pnl), -999999) DESC, best_score DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            decisions = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT action, reason, COUNT(*) AS count
                    FROM decisions
                    WHERE created_at>=?
                    GROUP BY action, reason
                    ORDER BY count DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            sessions = int(
                con.execute(
                    """
                    SELECT COUNT(DISTINCT substr(created_at,1,10))
                    FROM predictions
                    WHERE formal_anchor=1 AND created_at>=?
                    """,
                    (cutoff,),
                ).fetchone()[0]
                or 0
            )
            revision_summary = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT status, COUNT(*) AS count, AVG(ABS(revision_bps)) AS mean_abs_revision_bps
                    FROM data_revision_checks
                    WHERE checked_at>=?
                    GROUP BY status
                    ORDER BY count DESC
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            position_summary = dict(
                con.execute(
                    """
                    SELECT COUNT(*) AS positions,
                           SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed_positions,
                           SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS winners,
                           AVG(CASE WHEN status='CLOSED' THEN realized_pnl END) AS avg_realized_pnl,
                           SUM(CASE WHEN status='CLOSED' THEN realized_pnl ELSE 0 END) AS total_realized_pnl,
                           MIN(CASE WHEN status='CLOSED' THEN realized_pnl END) AS worst_trade,
                           MAX(CASE WHEN status='CLOSED' THEN realized_pnl END) AS best_trade
                    FROM positions
                    WHERE opened_at>=?
                    """,
                    (cutoff,),
                ).fetchone()
            )

        formal_metrics = self._metrics(formal_rows)
        all_metrics = self._metrics(all_rows)
        required_forecasts = self.config.audit.minimum_promotion_forecasts
        required_sessions = self.config.audit.minimum_promotion_sessions
        gates = {
            "minimum_formal_forecasts": formal_metrics["sample_size"] >= required_forecasts,
            "minimum_independent_sessions": sessions >= required_sessions,
            "direction_accuracy": (formal_metrics["direction_accuracy"] or 0.0) >= 0.52,
            "brier_score": (formal_metrics["brier"] if formal_metrics["brier"] is not None else 1.0) <= 0.25,
            "verified_integrity": (
                formal_metrics["integrity_verified_pct"]
                if formal_metrics["integrity_verified_pct"] is not None
                else 0.0
            ) >= 0.95,
        }
        promotion_status = {
            "eligible_for_manual_challenger_review": all(gates.values()),
            "automatic_promotion": False,
            "manual_review_required": True,
            "gates": gates,
            "formal_forecasts": formal_metrics["sample_size"],
            "independent_sessions": sessions,
            "minimum_forecasts": required_forecasts,
            "minimum_sessions": required_sessions,
        }

        report = {
            "generated_at": utc_iso(),
            "window": {"recent_days": recent_days, "cutoff_utc": cutoff},
            "formal_confirmation_metrics": formal_metrics,
            "diagnostic_all_prediction_metrics": all_metrics,
            "independent_session_count": sessions,
            "data_revision_summary": revision_summary,
            "strategy_summary": strategies,
            "decision_summary": decisions,
            "position_summary": position_summary,
            "promotion_status": promotion_status,
            "governance": {
                "live_model_changed": False,
                "live_configuration_changed": False,
                "purpose": "after-hours evidence and manual champion/challenger governance",
            },
        }
        (output_dir / "summary.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

        markdown = [
            "# Alpha-SPY Dojo Report",
            "",
            f"Generated: {report['generated_at']}",
            f"Evidence window: last {recent_days} days (from {cutoff})",
            "",
            "The dojo did not change the live model or configuration.",
            "",
            "## Formal non-overlapping confirmation metrics",
            "",
            "```json",
            json.dumps(formal_metrics, indent=2, default=str),
            "```",
            "",
            "## Promotion gates",
            "",
            "```json",
            json.dumps(promotion_status, indent=2, default=str),
            "```",
            "",
            "## Managed-position results",
            "",
            "```json",
            json.dumps(position_summary, indent=2, default=str),
            "```",
            "",
            "## Strategy counterfactual summary",
            "",
        ]
        for row in strategies:
            avg_pnl = row.get("avg_realized_pnl")
            win_rate = row.get("realized_win_rate")
            markdown.append(
                f"- **{row['strategy']}**: {row['candidates']} candidates; "
                f"{row.get('matured') or 0} matured; "
                f"avg modeled EV {float(row.get('avg_expected_value') or 0.0):.2f}; "
                f"avg T+15 P&L {float(avg_pnl or 0.0):.2f}; "
                f"T+15 win rate {float(win_rate or 0.0):.1%}"
            )
        markdown.extend(["", "## Data revision summary", "", "```json", json.dumps(revision_summary, indent=2, default=str), "```", ""])
        (output_dir / "REPORT.md").write_text("\n".join(markdown), encoding="utf-8")
        self.journal.heartbeat("dojo", "ONLINE", None, str(output_dir))
        return output_dir


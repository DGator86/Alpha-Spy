from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd

from .config import SuiteConfig


@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str
    sector: str
    weight: float

    def as_dict(self) -> dict:
        return asdict(self)


FALLBACK_HOLDINGS = [
    Holding("AAPL", "Apple Inc", "Information Technology", 0.0780),
    Holding("NVDA", "NVIDIA Corp", "Information Technology", 0.0760),
    Holding("MSFT", "Microsoft Corp", "Information Technology", 0.0460),
    Holding("AMZN", "Amazon.com Inc", "Consumer Discretionary", 0.0380),
    Holding("GOOGL", "Alphabet Class A", "Communication Services", 0.0310),
    Holding("AVGO", "Broadcom Inc", "Information Technology", 0.0280),
    Holding("GOOG", "Alphabet Class C", "Communication Services", 0.0250),
    Holding("META", "Meta Platforms", "Communication Services", 0.0240),
    Holding("TSLA", "Tesla Inc", "Consumer Discretionary", 0.0190),
    Holding("BRK/B", "Berkshire Hathaway B", "Financials", 0.0180),
    Holding("JPM", "JPMorgan Chase", "Financials", 0.0160),
    Holding("LLY", "Eli Lilly", "Health Care", 0.0150),
    Holding("WMT", "Walmart", "Consumer Staples", 0.0130),
    Holding("V", "Visa", "Financials", 0.0120),
    Holding("XOM", "Exxon Mobil", "Energy", 0.0120),
    Holding("MA", "Mastercard", "Financials", 0.0110),
    Holding("COST", "Costco", "Consumer Staples", 0.0100),
    Holding("JNJ", "Johnson & Johnson", "Health Care", 0.0090),
    Holding("HD", "Home Depot", "Consumer Discretionary", 0.0090),
    Holding("PG", "Procter & Gamble", "Consumer Staples", 0.0090),
]


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    symbol = symbol.replace(".", "/")
    symbol = re.sub(r"\s+", "", symbol)
    return symbol


def _clean_sector(value: object) -> str:
    """Normalise a missing sector.

    SSGA's daily holdings sheet carries "-" in every Sector cell, which would
    otherwise be stored verbatim. The live runtime records sector but does not
    compute on it, so an honest "Unknown" is preferable to a stray dash.
    """
    text = str(value or "").strip()
    return text if text and text not in {"-", "--", "N/A", "n/a"} else "Unknown"


def _parse_csv_text(text: str) -> list[Holding]:
    # iShares CSVs contain metadata before the header. Locate the row beginning with Ticker.
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        first = line.lstrip("\ufeff").split(",", 1)[0].strip('" ').lower()
        if first == "ticker":
            start = index
            break
    if start is None:
        raise ValueError("Could not locate Ticker header in holdings CSV")

    csv_text = "\n".join(lines[start:])
    frame = pd.read_csv(io.StringIO(csv_text))
    column_map = {str(c).strip().lower(): c for c in frame.columns}

    ticker_col = column_map.get("ticker")
    name_col = column_map.get("name")
    sector_col = column_map.get("sector")
    weight_col = column_map.get("weight (%)") or column_map.get("weight")
    asset_col = column_map.get("asset class")

    if ticker_col is None or weight_col is None:
        raise ValueError(f"Required holdings columns missing: {list(frame.columns)}")

    holdings: list[Holding] = []
    for _, row in frame.iterrows():
        ticker = normalize_symbol(str(row.get(ticker_col, "")))
        if not ticker or ticker in {"-", "NAN", "CASH", "USD"}:
            continue
        if asset_col is not None:
            asset_class = str(row.get(asset_col, "")).strip().lower()
            if asset_class and asset_class not in {"equity", "stock"}:
                continue
        try:
            raw_weight = str(row.get(weight_col, "0")).replace("%", "").replace(",", "")
            weight = float(raw_weight) / 100.0
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        holdings.append(
            Holding(
                symbol=ticker,
                name=str(row.get(name_col, ticker)).strip() if name_col else ticker,
                sector=_clean_sector(row.get(sector_col)) if sector_col else "Unknown",
                weight=weight,
            )
        )

    holdings.sort(key=lambda h: h.weight, reverse=True)
    total = sum(h.weight for h in holdings)
    if total <= 0:
        raise ValueError("Holdings weights sum to zero")
    # Preserve ETF weights while normalizing small cash drift.
    if 0.95 <= total <= 1.05:
        holdings = [Holding(h.symbol, h.name, h.sector, h.weight / total) for h in holdings]
    return holdings


def _parse_holdings_xlsx(payload: bytes) -> list[Holding]:
    """Parse an SSGA-style holdings workbook.

    SSGA publishes SPY's own holdings as xlsx with a preamble above the header
    row, so the header is located rather than assumed.
    """
    raw = pd.read_excel(io.BytesIO(payload), header=None)
    header_row = None
    for index in range(min(25, len(raw))):
        cells = [str(cell).strip().lower() for cell in raw.iloc[index].tolist()]
        if "ticker" in cells and any("weight" in cell for cell in cells):
            header_row = index
            break
    if header_row is None:
        raise ValueError("Could not locate the Ticker/Weight header in the holdings workbook")

    frame = pd.read_excel(io.BytesIO(payload), header=header_row)
    frame.columns = [str(column).strip() for column in frame.columns]
    # _parse_csv_text locates the header by a row starting with "Ticker";
    # SSGA orders the sheet Name, Ticker, ... so move it to the front.
    ticker = next(c for c in frame.columns if c.strip().lower() == "ticker")
    frame = frame[[ticker, *[c for c in frame.columns if c != ticker]]]
    return _parse_csv_text(frame.to_csv(index=False))


def read_local_csv(path: Path) -> list[Holding]:
    with path.open("r", encoding="utf-8-sig") as handle:
        text = handle.read()
    try:
        return _parse_csv_text(text)
    except ValueError:
        # Also accept the suite's compact format.
        holdings: list[Holding] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            symbol = normalize_symbol(row.get("symbol") or row.get("ticker") or "")
            if not symbol:
                continue
            raw_weight = row.get("weight", "0")
            weight = float(raw_weight)
            if weight > 1.0:
                weight /= 100.0
            holdings.append(
                Holding(
                    symbol=symbol,
                    name=row.get("name", symbol),
                    sector=row.get("sector", "Unknown"),
                    weight=weight,
                )
            )
        holdings.sort(key=lambda h: h.weight, reverse=True)
        return holdings


def write_compact_csv(path: Path, holdings: Iterable[Holding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "name", "sector", "weight"])
        writer.writeheader()
        for holding in holdings:
            writer.writerow(holding.as_dict())


class UniverseProvider:
    def __init__(self, config: SuiteConfig):
        self.config = config

    def cache_is_fresh(self) -> bool:
        path = self.config.paths.universe_cache
        if not path.exists():
            return False
        age = datetime.now(UTC).timestamp() - path.stat().st_mtime
        return age <= self.config.universe.maximum_age_hours * 3600

    def _meets_minimum(self, holdings: list[Holding]) -> bool:
        return (
            len(holdings) >= self.config.universe.minimum_symbols
            and sum(h.weight for h in holdings) >= self.config.universe.minimum_covered_weight
        )

    def refresh(self, force: bool = False) -> list[Holding]:
        # A cached universe is only reusable if it still satisfies the
        # configured coverage. The previous `min(20, minimum_symbols)` meant a
        # 20-symbol cache counted as fresh even at a minimum of 450, so a
        # single failed fetch pinned the system to a stub universe until the
        # cache aged out -- with entries blocked the whole time.
        if not force and self.cache_is_fresh():
            cached = read_local_csv(self.config.paths.universe_cache)
            if self._meets_minimum(cached):
                return cached

        source = self.config.universe.source
        holdings: list[Holding] = []
        errors: list[str] = []
        if source == "local_csv":
            holdings = read_local_csv(self.config.universe.local_csv)
        elif source == "fallback":
            holdings = list(FALLBACK_HOLDINGS)
        else:
            for label, url in self._remote_sources():
                try:
                    holdings = self._fetch(url)
                    break
                except Exception as exc:
                    errors.append(f"{label}: {exc}")

            if not self._meets_minimum(holdings):
                # Prefer any on-disk universe that actually satisfies coverage
                # over a short remote answer.
                for label, path in (
                    ("cache", self.config.paths.universe_cache),
                    ("local_csv", self.config.universe.local_csv),
                ):
                    if not path.exists():
                        continue
                    try:
                        candidate = read_local_csv(path)
                    except Exception as exc:
                        errors.append(f"{label}: {exc}")
                        continue
                    if self._meets_minimum(candidate):
                        holdings = candidate
                        break
                    if not holdings:
                        holdings = candidate

        if not holdings:
            holdings = list(FALLBACK_HOLDINGS)

        if not holdings:
            raise RuntimeError("Universe is empty")

        # Record why coverage is short so the health check and the operator see
        # a reason rather than an unexplained blocked session.
        self.last_errors = errors
        self.meets_minimum = self._meets_minimum(holdings)
        write_compact_csv(self.config.paths.universe_cache, holdings)
        return holdings

    def _remote_sources(self) -> list[tuple[str, str]]:
        """The configured source first, then any declared alternates."""
        sources = [("source_url", self.config.universe.source_url)]
        sources.extend(
            (f"fallback_url[{i}]", url)
            for i, url in enumerate(self.config.universe.fallback_source_urls)
        )
        return [(label, url) for label, url in sources if url]

    @staticmethod
    def _fetch(url: str) -> list[Holding]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/csv,application/vnd.ms-excel,text/plain,*/*",
        }
        with httpx.Client(timeout=45.0, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            response.raise_for_status()
            body = response.content

        # A 200 is not enough. iShares answers a blocked server-side fetch with
        # its product page under `Content-Type: text/csv`, so the status and
        # the header both lie; only the body settles it.
        if body[:2] == b"PK":
            return _parse_holdings_xlsx(body)
        text = body.decode("utf-8-sig", errors="replace")
        if re.match(r"\s*<(?:!doctype|html)\b", text, re.I):
            raise ValueError(f"{url} returned an HTML document rather than holdings data")
        return _parse_csv_text(text)

    def get(self) -> list[Holding]:
        return self.refresh(force=False)

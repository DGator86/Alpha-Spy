"""The constituent universe must actually reach the configured coverage.

The suite is configured to operate at 450 symbols and 0.90 covered weight, and
blocks every entry below that. These pin the guarantee end to end: the bundled
file satisfies it offline, a short universe is never silently accepted, and a
blocked source cannot masquerade as data.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from spy_der.config import SuiteConfig, UniverseConfig
from spy_der.universe import (
    Holding,
    UniverseProvider,
    _clean_sector,
    _parse_csv_text,
    read_local_csv,
    write_compact_csv,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED = REPO_ROOT / "config" / "universe.csv"
SHIPPED_CONFIG = yaml.safe_load((REPO_ROOT / "config" / "suite.yaml").read_text(encoding="utf-8"))
MIN_SYMBOLS = SHIPPED_CONFIG["universe"]["minimum_symbols"]
MIN_WEIGHT = SHIPPED_CONFIG["universe"]["minimum_covered_weight"]


def _bundled() -> list[dict]:
    with BUNDLED.open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_bundled_universe_satisfies_the_shipped_minimum():
    """Offline, the suite must still operate at the configured coverage.

    The previous bundled file carried 20 symbols and 0.499 weight against a
    minimum of 450/0.90, so any failure of the remote source pinned the system
    below its own gate and blocked trading for the session.
    """
    rows = _bundled()
    weight = sum(float(r["weight"]) for r in rows)
    assert len(rows) >= MIN_SYMBOLS, f"bundled universe has {len(rows)} symbols, need {MIN_SYMBOLS}"
    assert weight >= MIN_WEIGHT, f"bundled universe covers {weight:.4f}, need {MIN_WEIGHT}"


def test_bundled_universe_is_well_formed():
    rows = _bundled()
    symbols = [r["symbol"] for r in rows]
    assert len(symbols) == len(set(symbols)), "duplicate symbols in the bundled universe"
    assert all(r["symbol"].strip() for r in rows), "blank symbol in the bundled universe"
    assert all(0.0 < float(r["weight"]) < 0.25 for r in rows), "implausible constituent weight"
    weight = sum(float(r["weight"]) for r in rows)
    assert weight == pytest.approx(1.0, abs=0.02), f"weights sum to {weight:.4f}"


def test_bundled_universe_loads_through_the_provider(tmp_path):
    holdings = read_local_csv(BUNDLED)
    assert len(holdings) >= MIN_SYMBOLS
    assert sum(h.weight for h in holdings) >= MIN_WEIGHT
    assert holdings == sorted(holdings, key=lambda h: h.weight, reverse=True)


def _provider(tmp_path, **universe) -> UniverseProvider:
    config = SuiteConfig()
    config.paths.state_root = tmp_path
    config.paths.universe_cache = tmp_path / "reference" / "universe.csv"
    config.universe = UniverseConfig(**{"local_csv": BUNDLED, **universe})
    return UniverseProvider(config)


def test_local_csv_source_reaches_the_minimum(tmp_path):
    holdings = _provider(tmp_path, source="local_csv").refresh(force=True)
    assert len(holdings) >= MIN_SYMBOLS
    assert sum(h.weight for h in holdings) >= MIN_WEIGHT


def test_a_short_cache_is_not_reused(tmp_path):
    """Regression: the freshness check capped the requirement at 20 symbols.

    `len(holdings) >= min(20, minimum_symbols)` meant a stub cache counted as
    fresh at a minimum of 450, so one failed fetch pinned the system to it
    until the cache aged out.
    """
    provider = _provider(tmp_path, source="local_csv", minimum_symbols=450)
    stub = [Holding("AAPL", "Apple", "Unknown", 0.5), Holding("MSFT", "Microsoft", "Unknown", 0.4)]
    write_compact_csv(provider.config.paths.universe_cache, stub)
    assert provider.cache_is_fresh()

    holdings = provider.refresh(force=False)
    assert len(holdings) >= MIN_SYMBOLS, "a short cache was reused instead of being replaced"


def test_html_masquerading_as_csv_is_rejected():
    """iShares answers a blocked server-side fetch with its product page under
    Content-Type: text/csv, so only the body can be trusted."""
    html = "<!DOCTYPE html>\n<html><head><title>iShares</title></head><body>...</body></html>"
    with pytest.raises(ValueError, match="Ticker"):
        _parse_csv_text(html)


def test_fetch_rejects_an_html_body(monkeypatch):
    import httpx

    class _Response:
        content = b"<!doctype html><html><body>blocked</body></html>"

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Response()

    monkeypatch.setattr(httpx, "Client", _Client)
    with pytest.raises(ValueError, match="HTML document"):
        UniverseProvider._fetch("https://example.invalid/holdings.csv")


@pytest.mark.parametrize(
    "raw,expected",
    [("-", "Unknown"), ("--", "Unknown"), ("", "Unknown"), (None, "Unknown"),
     ("Information Technology", "Information Technology"), ("  Energy  ", "Energy")],
)
def test_missing_sectors_normalise(raw, expected):
    assert _clean_sector(raw) == expected


def test_shipped_config_points_at_a_working_source():
    universe = SHIPPED_CONFIG["universe"]
    assert universe["source"] == "ssga_spy"
    assert "ssga.com" in universe["source_url"]
    # The iShares endpoint is retained only as a declared alternate.
    assert any("ishares" in url for url in universe.get("fallback_source_urls", []))

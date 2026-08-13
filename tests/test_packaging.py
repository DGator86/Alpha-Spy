"""Tests that the wheel actually ships the workstation.

The dashboard's static bundle is committed and shipped inside the wheel, so a
VPS never needs a Node toolchain. That only works if setuptools' package-data
patterns match every file in it. Getting this wrong fails silently: pip reports
a successful install and the dashboard serves a broken or stale page.
"""
from __future__ import annotations

import glob
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "src" / "alpha_spy" / "dashboard" / "static"


def _patterns() -> list[str]:
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["setuptools"]["package-data"]["alpha_spy.dashboard"]


def _matched(recursive: bool) -> set[Path]:
    package_dir = STATIC.parent
    matched: set[Path] = set()
    for pattern in _patterns():
        for hit in glob.glob(str(package_dir / pattern), recursive=recursive):
            path = Path(hit)
            if path.is_file():
                matched.add(path.resolve())
    return matched


def _shipped() -> set[Path]:
    return {p.resolve() for p in STATIC.rglob("*") if p.is_file()}


def test_static_bundle_is_present():
    # A missing bundle means someone deleted it or never ran `make frontend`.
    assert (STATIC / "index.html").is_file(), "static/index.html is missing; run 'make frontend'"
    assert list((STATIC / "assets").glob("*.js")), "static/assets holds no scripts; run 'make frontend'"


def test_package_data_covers_every_shipped_file_without_recursive_glob():
    """The regression this file exists for.

    setuptools expands package-data with `glob`, and only passes
    recursive=True in newer versions. Under the non-recursive behaviour `**`
    collapses to a single level, so "static/**/*" matches static/assets/* but
    silently drops static/index.html — a wheel with the scripts and no page.
    Requiring full coverage under the stricter semantics keeps the patterns
    correct for every setuptools the suite might be built with.
    """
    missing = _shipped() - _matched(recursive=False)
    assert not missing, (
        "these files would be left out of the wheel by an older setuptools: "
        + ", ".join(sorted(str(p.relative_to(STATIC)) for p in missing))
    )


def test_package_data_covers_every_shipped_file_with_recursive_glob():
    missing = _shipped() - _matched(recursive=True)
    assert not missing, (
        "these files would be left out of the wheel: "
        + ", ".join(sorted(str(p.relative_to(STATIC)) for p in missing))
    )


def test_index_html_references_only_bundled_assets():
    """The page and the files it loads have to ship together.

    Hashed filenames change on every rebuild, so a committed index.html
    pointing at an asset that is no longer on disk is the exact shape of a
    stale-bundle mistake — and it serves a blank page rather than an error.
    """
    import re

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    referenced = set(re.findall(r'/static/(assets/[A-Za-z0-9._-]+)', html))
    assert referenced, "index.html references no bundled assets at all"
    for asset in sorted(referenced):
        assert (STATIC / asset).is_file(), f"index.html references {asset}, which is not committed"

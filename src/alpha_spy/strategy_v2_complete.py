from __future__ import annotations

from itertools import combinations
from typing import Any

from . import strategy_v2 as base


_BASE_ENUMERATOR = base.enumerate_bounded_risk_specs


def _flip(side: str) -> str:
    return "sell_to_open" if side.startswith("buy") else "buy_to_open"


def enumerate_bounded_risk_specs(
    options: list[dict[str, Any]],
    spot: float,
    cfg: base.V2OptimizerConfig | None = None,
) -> list[base.StructureSpec]:
    """Complete the base V2 universe so it matches the 47-family oracle exactly."""
    cfg = cfg or base.V2OptimizerConfig()
    original = _BASE_ENUMERATOR(options, spot, cfg)
    calls = sorted(
        [row for row in options if row["right"] == "C"],
        key=lambda row: float(row["strike"]),
    )
    puts = sorted(
        [row for row in options if row["right"] == "P"],
        key=lambda row: float(row["strike"]),
    )

    repaired: list[base.StructureSpec] = []
    for spec in original:
        # The early V2 prototype mislabeled asymmetric four-strike condors as
        # Christmas trees. Drop those labels; proper three-strike ratio trees
        # are generated below.
        if spec.name in {
            "CALL_CHRISTMAS_TREE",
            "PUT_CHRISTMAS_TREE",
            "REVERSE_CALL_CHRISTMAS_TREE",
            "REVERSE_PUT_CHRISTMAS_TREE",
        }:
            continue
        if spec.name in {"IRON_BUTTERFLY", "REVERSE_IRON_BUTTERFLY"}:
            strikes = sorted(float(option["strike"]) for option, _, _ in spec.legs)
            if len(strikes) == 4:
                center = 0.5 * (strikes[1] + strikes[2])
                left = center - strikes[0]
                right = strikes[3] - center
                if abs(left - right) > 0.51:
                    name = (
                        "REVERSE_BROKEN_WING_IRON_BUTTERFLY"
                        if spec.name.startswith("REVERSE_")
                        else "BROKEN_WING_IRON_BUTTERFLY"
                    )
                    repaired.append(base.StructureSpec(name, spec.legs, spec.width))
                    continue
        repaired.append(spec)

    for rows, prefix in ((calls, "CALL"), (puts, "PUT")):
        for a, b, c in combinations(rows, 3):
            left = float(b["strike"]) - float(a["strike"])
            right = float(c["strike"]) - float(b["strike"])
            if left <= 0 or right <= 0 or max(left, right) > cfg.max_width:
                continue
            if prefix == "CALL":
                tree = (
                    (a, "buy_to_open", 1),
                    (b, "sell_to_open", 3),
                    (c, "buy_to_open", 2),
                )
            else:
                tree = (
                    (a, "buy_to_open", 2),
                    (b, "sell_to_open", 3),
                    (c, "buy_to_open", 1),
                )
            reverse = tuple((option, _flip(side), quantity) for option, side, quantity in tree)
            repaired.append(
                base.StructureSpec(f"{prefix}_CHRISTMAS_TREE", tree, max(left, right))
            )
            repaired.append(
                base.StructureSpec(
                    f"REVERSE_{prefix}_CHRISTMAS_TREE",
                    reverse,
                    max(left, right),
                )
            )

    seen: set[tuple[Any, ...]] = set()
    unique: list[base.StructureSpec] = []
    for spec in repaired:
        key = (
            spec.name,
            tuple(
                (str(option["symbol"]), side, quantity)
                for option, side, quantity in spec.legs
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(spec)
    return unique


# `base.generate_v2_candidates` resolves this global from its own module each
# call. Patch it once when the authoritative V2 runtime imports this module.
base.enumerate_bounded_risk_specs = enumerate_bounded_risk_specs

generate_v2_candidates = base.generate_v2_candidates
V2OptimizerConfig = base.V2OptimizerConfig
StructureSpec = base.StructureSpec
liquid_contract_pool = base.liquid_contract_pool

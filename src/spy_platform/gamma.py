from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .contracts import GammaState, ModelMeta


def _float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _median(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    return float(statistics.median(rows)) if rows else None


def _parse_timestamp(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _expiry_days(expiration: str, now: datetime) -> float:
    expiry = datetime.fromisoformat(expiration).replace(tzinfo=UTC, hour=21, minute=0)
    return max(0.0, (expiry - now).total_seconds() / 86400.0)


def _right(row: dict[str, Any]) -> str:
    value = str(row.get("right") or row.get("option_type") or "").upper()
    return "C" if value.startswith("C") else "P" if value.startswith("P") else ""


def _spread_bps(row: dict[str, Any]) -> float | None:
    bid = _float(row.get("bid"))
    ask = _float(row.get("ask"))
    if bid is None or ask is None or bid < 0 or ask <= bid:
        return None
    mid = 0.5 * (bid + ask)
    return 10_000.0 * (ask - bid) / mid if mid > 0 else None


def _hhi(rows: list[dict[str, Any]], field: str) -> float | None:
    weights = [max(_float(row.get(field)) or 0.0, 0.0) for row in rows]
    total = sum(weights)
    if total <= 0:
        return None
    return float(sum((value / total) ** 2 for value in weights))


def _closest_delta_iv(rows: list[dict[str, Any]], target: float) -> float | None:
    candidates: list[tuple[float, float]] = []
    for row in rows:
        delta = _float(row.get("delta"))
        iv = _float(row.get("iv"))
        if delta is None or iv is None or iv <= 0:
            continue
        candidates.append((abs(delta - target), iv))
    return min(candidates, default=(0.0, None), key=lambda item: item[0])[1]


def _gamma_proxy(rows: list[dict[str, Any]], spot: float) -> dict[str, Any]:
    by_strike: dict[float, float] = defaultdict(float)
    gross = 0.0
    net = 0.0
    contracts = 0
    for row in rows:
        gamma = _float(row.get("gamma"))
        oi = _float(row.get("open_interest"))
        strike = _float(row.get("strike"))
        right = _right(row)
        if gamma is None or oi is None or strike is None or oi <= 0 or right not in {"C", "P"}:
            continue
        signed = (1.0 if right == "C" else -1.0) * gamma * oi * 100.0 * spot * spot * 0.01
        by_strike[strike] += signed
        gross += abs(signed)
        net += signed
        contracts += 1

    normalized = net / gross if gross > 0 else None
    crossing = None
    running = 0.0
    previous_sign = 0
    for strike in sorted(by_strike):
        running += by_strike[strike]
        sign = 1 if running > 0 else -1 if running < 0 else 0
        if previous_sign and sign and sign != previous_sign:
            crossing = strike
            break
        if sign:
            previous_sign = sign

    return {
        "source": "call_put_oi_signed_gamma_proxy",
        "dealer_inventory_observed": False,
        "contracts": contracts,
        "gross": gross,
        "net": net,
        "normalized": normalized,
        "flip_strike_proxy": crossing,
        "by_strike": dict(sorted(by_strike.items())),
    }


def build_gamma_state(
    *,
    timestamp: datetime | str,
    spot: float,
    chains: list[dict[str, Any]],
    model_version: str = "gamma-spy-v0.1",
) -> GammaState:
    """Compile observable derivatives state without selecting a trade.

    `chains` accepts multiple expirations. Each item should contain `expiration`
    and `options`; rows use Alpha's normalized option fields when available.
    """
    now = _parse_timestamp(timestamp)
    valid_chains = [
        chain
        for chain in chains
        if isinstance(chain.get("options"), list) and chain.get("expiration")
    ]
    all_rows = [row for chain in valid_chains for row in chain["options"] if isinstance(row, dict)]

    expiry_surface: list[dict[str, Any]] = []
    for chain in valid_chains:
        expiration = str(chain["expiration"])
        rows = list(chain["options"])
        near = [
            row
            for row in rows
            if (_float(row.get("strike")) is not None)
            and spot > 0
            and abs((_float(row.get("strike")) or spot) / spot - 1.0) <= 0.01
        ]
        sample = near or rows
        call_rows = [row for row in rows if _right(row) == "C"]
        put_rows = [row for row in rows if _right(row) == "P"]
        atm_iv = _median(
            iv
            for row in sample
            if (iv := _float(row.get("iv"))) is not None and iv > 0
        )
        call25 = _closest_delta_iv(call_rows, 0.25)
        put25 = _closest_delta_iv(put_rows, -0.25)
        expiry_surface.append(
            {
                "expiration": expiration,
                "dte": _expiry_days(expiration, now),
                "atm_iv": atm_iv,
                "put25_iv": put25,
                "call25_iv": call25,
                "put_call_25d_skew": (put25 - call25) if put25 is not None and call25 is not None else None,
                "open_interest_hhi": _hhi(rows, "open_interest"),
                "volume_hhi": _hhi(rows, "volume"),
                "contract_rows": len(rows),
            }
        )
    expiry_surface.sort(key=lambda row: row["dte"])

    call_volume = sum(max(_float(row.get("volume")) or 0.0, 0.0) for row in all_rows if _right(row) == "C")
    put_volume = sum(max(_float(row.get("volume")) or 0.0, 0.0) for row in all_rows if _right(row) == "P")
    call_oi = sum(max(_float(row.get("open_interest")) or 0.0, 0.0) for row in all_rows if _right(row) == "C")
    put_oi = sum(max(_float(row.get("open_interest")) or 0.0, 0.0) for row in all_rows if _right(row) == "P")
    spread_rows = [value for row in all_rows if (value := _spread_bps(row)) is not None]
    gamma = _gamma_proxy(all_rows, float(spot))

    strike_oi: dict[float, float] = defaultdict(float)
    for row in all_rows:
        strike = _float(row.get("strike"))
        oi = _float(row.get("open_interest"))
        if strike is not None and oi is not None and oi > 0:
            strike_oi[strike] += oi
    pin_strike = max(strike_oi, key=strike_oi.get) if strike_oi else None
    total_oi = sum(strike_oi.values())
    pin_concentration = strike_oi.get(pin_strike, 0.0) / total_oi if pin_strike is not None and total_oi > 0 else None

    ivs = [row for row in expiry_surface if row["atm_iv"] is not None]
    term_slope = None
    if len(ivs) >= 2:
        short, long = ivs[0], ivs[-1]
        day_gap = max(float(long["dte"]) - float(short["dte"]), 1e-9)
        term_slope = (float(long["atm_iv"]) - float(short["atm_iv"])) / day_gap

    quality_parts = [
        1.0 if valid_chains else 0.0,
        min(1.0, len(all_rows) / 100.0),
        min(1.0, len(expiry_surface) / 3.0),
        min(1.0, len(spread_rows) / max(len(all_rows), 1) / 0.5),
    ]
    quality = sum(quality_parts) / len(quality_parts)

    return GammaState(
        meta=ModelMeta.create(
            model="GAMMA",
            timestamp=now,
            model_version=model_version,
            data_quality=quality,
        ),
        directional_score=None,
        iv_surface={"expirations": expiry_surface},
        term_structure={"atm_iv_slope_per_day": term_slope},
        skew={
            "front_put_call_25d_skew": expiry_surface[0]["put_call_25d_skew"] if expiry_surface else None,
        },
        activity={
            "call_volume": call_volume,
            "put_volume": put_volume,
            "put_call_volume_ratio": put_volume / call_volume if call_volume > 0 else None,
            "call_open_interest": call_oi,
            "put_open_interest": put_oi,
            "put_call_open_interest_ratio": put_oi / call_oi if call_oi > 0 else None,
            "aggressor_side_observed": False,
            "interpretation": "unsigned_chain_activity_not_order_flow",
        },
        positioning={
            "gamma": gamma,
            "pin_strike_proxy": pin_strike,
            "pin_open_interest_concentration": pin_concentration,
            "vanna_available": False,
            "charm_available": False,
        },
        liquidity={
            "median_relative_spread_bps": _median(spread_rows),
            "quoted_contract_fraction": len(spread_rows) / max(len(all_rows), 1),
        },
        risk_states={
            "negative_gamma_proxy": (gamma["normalized"] or 0.0) < -0.10 if gamma["normalized"] is not None else None,
            "positive_gamma_proxy": (gamma["normalized"] or 0.0) > 0.10 if gamma["normalized"] is not None else None,
        },
        metrics={
            "chain_count": len(valid_chains),
            "option_row_count": len(all_rows),
            "spot": float(spot),
        },
    )

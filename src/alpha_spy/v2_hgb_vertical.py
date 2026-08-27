from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from .timeutil import utc_iso


@dataclass(frozen=True)
class HGBVerticalConfig:
    width: float = 2.0
    max_atm_distance: float = 0.75
    max_absolute_spread: float = 0.03
    max_relative_spread: float = 0.20
    min_open_interest: int = 25
    min_volume: int = 1
    min_displayed_size: int = 1
    entry_mid_capture_fraction: float = 0.65
    estimated_pass_through_fee_per_contract_side: float = 0.03
    max_estimated_execution_drag_dollars: float = 6.00
    max_risk_dollars: float = 100.0


def _mid(option: dict[str, Any]) -> float:
    bid = float(option.get("bid") or 0.0)
    ask = float(option.get("ask") or 0.0)
    midpoint = option.get("midpoint")
    if midpoint not in (None, ""):
        try:
            value = float(midpoint)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0.5 * (bid + ask)


def _displayed_size(option: dict[str, Any], side: str) -> int:
    direct = option.get(f"{side}_size")
    if direct not in (None, ""):
        try:
            return max(0, int(float(direct)))
        except (TypeError, ValueError):
            pass
    payload = option.get("payload") or {}
    raw_key = "bidsize" if side == "bid" else "asksize"
    try:
        return max(0, int(float(payload.get(raw_key) or 0)))
    except (TypeError, ValueError, AttributeError):
        return 0


def _liquid(option: dict[str, Any], cfg: HGBVerticalConfig) -> bool:
    bid = float(option.get("bid") or 0.0)
    ask = float(option.get("ask") or 0.0)
    mid = _mid(option)
    if bid <= 0 or ask <= bid or mid <= 0:
        return False
    spread = ask - bid
    if spread > cfg.max_absolute_spread + 1e-9:
        return False
    if spread / max(mid, 0.01) > cfg.max_relative_spread + 1e-9:
        return False
    if int(option.get("open_interest") or 0) < cfg.min_open_interest:
        return False
    if int(option.get("volume") or 0) < cfg.min_volume:
        return False
    if min(_displayed_size(option, "bid"), _displayed_size(option, "ask")) < cfg.min_displayed_size:
        return False
    return str(option.get("right") or "") in {"C", "P"}


def _pair_execution(
    long_leg: dict[str, Any], short_leg: dict[str, Any], cfg: HGBVerticalConfig
) -> dict[str, float]:
    long_mid = _mid(long_leg)
    short_mid = _mid(short_leg)
    natural_debit = float(long_leg["ask"]) - float(short_leg["bid"])
    mid_debit = long_mid - short_mid
    planned_debit = natural_debit + cfg.entry_mid_capture_fraction * (mid_debit - natural_debit)
    combined_spread = (
        float(long_leg["ask"]) - float(long_leg["bid"])
        + float(short_leg["ask"]) - float(short_leg["bid"])
    )
    fee_dollars = 4.0 * cfg.estimated_pass_through_fee_per_contract_side
    # Same execution convention used by the stress-tested research path: entry
    # captures 65% of midpoint improvement and exit is assumed to capture 50%.
    residual_spread_drag = 0.5 * combined_spread * (2.0 - cfg.entry_mid_capture_fraction - 0.50) * 100.0
    return {
        "natural_debit": natural_debit,
        "mid_debit": mid_debit,
        "planned_debit": planned_debit,
        "combined_spread": combined_spread,
        "estimated_roundtrip_fees_dollars": fee_dollars,
        "estimated_execution_drag_dollars": residual_spread_drag + fee_dollars,
    }


def _candidate_pairs(
    options: list[dict[str, Any]],
    *,
    spot: float,
    bullish: bool,
    cfg: HGBVerticalConfig,
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, float]]]:
    right = "C" if bullish else "P"
    rows = [row for row in options if row.get("right") == right and _liquid(row, cfg)]
    rows.sort(key=lambda row: float(row.get("strike") or 0.0))
    by_strike = {round(float(row["strike"]), 4): row for row in rows}
    pairs: list[tuple[dict[str, Any], dict[str, Any], dict[str, float]]] = []
    for long_leg in rows:
        long_strike = float(long_leg["strike"])
        if abs(long_strike - spot) > cfg.max_atm_distance + 1e-9:
            continue
        short_strike = long_strike + cfg.width if bullish else long_strike - cfg.width
        short_leg = by_strike.get(round(short_strike, 4))
        if short_leg is None:
            continue
        execution = _pair_execution(long_leg, short_leg, cfg)
        debit = execution["planned_debit"]
        if debit <= 0 or debit >= cfg.width:
            continue
        risk = debit * 100.0 + execution["estimated_roundtrip_fees_dollars"]
        if risk > cfg.max_risk_dollars + 1e-9:
            continue
        if execution["estimated_execution_drag_dollars"] > cfg.max_estimated_execution_drag_dollars + 1e-9:
            continue
        pairs.append((long_leg, short_leg, execution))
    return pairs


def build_hgb_vertical_candidate(
    prediction: dict[str, Any],
    beta_opportunity: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    config: HGBVerticalConfig | None = None,
) -> dict[str, Any] | None:
    """Build the authoritative low-friction 15m V2 expression.

    This deliberately does not use Alpha's legacy P/Q expected-value gate. The
    authority is the blocked walk-forward HGB direction signal; Alpha's job here
    is execution quality, bounded risk, and deterministic 15-minute management.
    """
    cfg = config or HGBVerticalConfig()
    hgb = beta_opportunity.get("hgb_direction") or {}
    if not isinstance(hgb, dict) or not bool(hgb.get("eligible")):
        return None
    direction = str(hgb.get("direction") or "")
    if direction not in {"BULLISH", "BEARISH"}:
        return None
    strength = float(hgb.get("strength") or 0.0)
    if strength < 0.35:
        return None
    spot = float(prediction.get("spy_price") or 0.0)
    if spot <= 0:
        return None

    bullish = direction == "BULLISH"
    pairs = _candidate_pairs(options, spot=spot, bullish=bullish, cfg=cfg)
    if not pairs:
        return None

    # Preserve the validated ATM geometry first; among equally ATM pairs choose
    # the one with the least estimated execution drag and then the tightest debit.
    long_leg, short_leg, execution = min(
        pairs,
        key=lambda row: (
            abs(float(row[0]["strike"]) - spot),
            float(row[2]["estimated_execution_drag_dollars"]),
            float(row[2]["planned_debit"]),
        ),
    )
    debit = float(execution["planned_debit"])
    fees = float(execution["estimated_roundtrip_fees_dollars"])
    max_loss = debit * 100.0 + fees
    max_profit = max(0.0, (cfg.width - debit) * 100.0 - fees)
    probability_up = float(hgb.get("probability_up") or 0.5)
    signal_hit_probability = probability_up if bullish else 1.0 - probability_up
    strategy = "CALL_DEBIT_SPREAD" if bullish else "PUT_DEBIT_SPREAD"
    expiration = str(long_leg.get("expiration") or short_leg.get("expiration") or "")
    legs = [
        {
            "symbol": str(long_leg["symbol"]),
            "right": str(long_leg["right"]),
            "strike": float(long_leg["strike"]),
            "side": "buy_to_open",
            "quantity": 1,
        },
        {
            "symbol": str(short_leg["symbol"]),
            "right": str(short_leg["right"]),
            "strike": float(short_leg["strike"]),
            "side": "sell_to_open",
            "quantity": 1,
        },
    ]
    return {
        "candidate_id": f"C-{uuid.uuid4().hex[:16]}",
        "prediction_id": prediction["prediction_id"],
        "created_at": utc_iso(),
        "strategy": strategy,
        "status": "ELIGIBLE",
        # Legacy DB fields are retained for compatibility but are explicitly not
        # the authority for this candidate. Do not fabricate P/Q EV calibration.
        "score": strength,
        "probability_profit": 0.0,
        "expected_value": 0.0,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "entry_price": debit,
        "entry_kind": "debit",
        "width": cfg.width,
        "expiration": expiration,
        "rejection_reason": None,
        "legs": legs,
        "payload": {
            "family": "directional_long",
            "authority": "beta_v2_hgb_blocked_walk_forward",
            "valuation_method": "validated_direction_plus_actual_chain_execution",
            "legacy_pq_ev_authority": False,
            "shadow_pq_optimizer": True,
            "forecast_horizon_minutes": 15,
            "force_horizon_exit": True,
            "entry_spot": spot,
            "signal_strength": strength,
            "signal_hit_probability": signal_hit_probability,
            "signal_probability_up": probability_up,
            "signal_expected_return_bps": float(hgb.get("expected_return_bps") or 0.0),
            "signal_model_version": str(hgb.get("model_version") or ""),
            "core_prediction_bps": float(hgb.get("core_prediction_bps") or 0.0),
            "breadth_prediction_bps": float(hgb.get("breadth_prediction_bps") or 0.0),
            "long_quote": {
                "symbol": long_leg["symbol"],
                "bid": float(long_leg["bid"]),
                "ask": float(long_leg["ask"]),
                "bid_size": _displayed_size(long_leg, "bid"),
                "ask_size": _displayed_size(long_leg, "ask"),
                "open_interest": int(long_leg.get("open_interest") or 0),
                "volume": int(long_leg.get("volume") or 0),
            },
            "short_quote": {
                "symbol": short_leg["symbol"],
                "bid": float(short_leg["bid"]),
                "ask": float(short_leg["ask"]),
                "bid_size": _displayed_size(short_leg, "bid"),
                "ask_size": _displayed_size(short_leg, "ask"),
                "open_interest": int(short_leg.get("open_interest") or 0),
                "volume": int(short_leg.get("volume") or 0),
            },
            "execution": {**execution, "config": asdict(cfg)},
        },
    }

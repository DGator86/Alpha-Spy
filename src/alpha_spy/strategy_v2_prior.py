from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

import numpy as np

from . import strategy_v2 as base


_BASE_GENERATE = base.generate_v2_candidates


def blend_beta_prior(prediction: dict[str, Any]) -> dict[str, Any]:
    """Softly blend Beta V2 evidence into Alpha's physical P distribution.

    Beta never chooses a strategy family. Its validated state may only nudge the
    location/scale of P, in proportion to trust/agreement. Q remains untouched.
    """
    out = deepcopy(prediction)
    payload = dict(out.get("payload") or {})
    beta = payload.get("beta_v2") or {}
    distribution = dict(payload.get("distribution") or {})
    if not isinstance(beta, dict) or not isinstance(distribution, dict):
        return out

    try:
        trust = float(beta.get("trust") or 0.0)
        agreement = float(beta.get("agreement") or 0.0)
        p_up = float(beta.get("probability_up") or 0.5)
        expected_abs_bps = float(beta.get("expected_abs_bps") or 0.0)
        spot = float(out.get("spy_price") or 0.0)
        alpha_mu = float(distribution.get("p_expected_return") or out.get("expected_return") or 0.0)
        alpha_sigma = max(
            float(distribution.get("p_volatility") or out.get("sigma_return") or 0.0),
            1e-6,
        )
    except (TypeError, ValueError):
        return out
    if spot <= 0.0:
        return out

    # Agreement controls how much directional information survives; trust controls
    # whether Beta is allowed to influence Alpha at all. Cap Beta at 45% authority.
    weight = float(np.clip(trust * (0.35 + 0.65 * agreement), 0.0, 0.45))
    if weight <= 0.0:
        return out

    beta_edge = float(np.clip(2.0 * p_up - 1.0, -1.0, 1.0))
    beta_abs = max(0.0, expected_abs_bps) / 10_000.0
    beta_mu = beta_edge * beta_abs
    # For a roughly centered distribution E|X| ~= sigma*sqrt(2/pi).
    beta_sigma = max(beta_abs * math.sqrt(math.pi / 2.0), 1e-6)
    target_mu = (1.0 - weight) * alpha_mu + weight * beta_mu
    target_sigma = max((1.0 - weight) * alpha_sigma + weight * beta_sigma, 1e-6)

    quantiles = np.asarray(distribution.get("p_price_quantiles") or [], dtype=float)
    if quantiles.size >= 5 and np.all(np.isfinite(quantiles)):
        alpha_returns = quantiles / spot - 1.0
        scaled = (alpha_returns - alpha_mu) * (target_sigma / alpha_sigma) + target_mu
        distribution["p_price_quantiles"] = (spot * np.maximum(1e-6, 1.0 + scaled)).tolist()
    distribution["p_expected_return"] = target_mu
    distribution["p_volatility"] = target_sigma
    distribution["p_source"] = f"{distribution.get('p_source') or 'alpha_P'}+beta_v2_validated_prior"

    out["expected_return"] = target_mu
    out["sigma_return"] = target_sigma
    z = target_mu / max(target_sigma, 1e-8)
    out["probability_up"] = float(1.0 / (1.0 + math.exp(-1.702 * z)))
    out["predicted_price"] = spot * (1.0 + target_mu)
    payload["distribution"] = distribution
    payload["beta_v2_prior_blend"] = {
        "weight": weight,
        "beta_probability_up": p_up,
        "beta_expected_abs_bps": expected_abs_bps,
        "beta_mu": beta_mu,
        "beta_sigma": beta_sigma,
        "alpha_mu_before": alpha_mu,
        "alpha_sigma_before": alpha_sigma,
        "p_mu_after": target_mu,
        "p_sigma_after": target_sigma,
        "q_unchanged": True,
        "strategy_authority": False,
    }
    out["payload"] = payload
    return out


def generate_v2_candidates(config, prediction, options, *, optimizer_config=None):
    return _BASE_GENERATE(
        config,
        blend_beta_prior(prediction),
        options,
        optimizer_config=optimizer_config,
    )


# Authoritative V2 runtime imports this before v2_services. The service's
# `from strategy_v2 import generate_v2_candidates` therefore receives this wrapper.
base.generate_v2_candidates = generate_v2_candidates

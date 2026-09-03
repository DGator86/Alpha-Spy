"""Shared market-intelligence platform for Alpha, Beta, Gamma, and Delta.

This package is intentionally non-executing.  It normalizes independent model
outputs, compiles market state, and publishes read-only analytical streams.
"""

from .contracts import AlphaState, BetaState, DeltaState, GammaState, ModelMeta
from .delta import compile_delta_state
from .gamma import build_gamma_state
from .streams import build_delta_streams

__all__ = [
    "AlphaState",
    "BetaState",
    "DeltaState",
    "GammaState",
    "ModelMeta",
    "build_delta_streams",
    "build_gamma_state",
    "compile_delta_state",
]

from __future__ import annotations

from collections.abc import Callable
from itertools import permutations

import numpy as np
import pandas as pd


def shapley_attribution(
    components: list[str],
    value_function: Callable[[frozenset[str]], float],
    *,
    max_exact_components: int = 8,
    monte_carlo_permutations: int = 2000,
    seed: int = 86,
) -> pd.Series:
    """Attribute an option-value residual to model components using Shapley values.

    `value_function(active_components)` must return the model value with only those components active.
    Exact enumeration is used for small models; otherwise permutation sampling is used.
    """
    if len(set(components)) != len(components):
        raise ValueError("Component names must be unique")
    contribution = {name: 0.0 for name in components}
    if len(components) <= max_exact_components:
        orders = list(permutations(components))
    else:
        rng = np.random.default_rng(seed)
        orders = [tuple(rng.permutation(components)) for _ in range(monte_carlo_permutations)]
    for order in orders:
        active: frozenset[str] = frozenset()
        previous = value_function(active)
        for name in order:
            active = frozenset(set(active) | {name})
            current = value_function(active)
            contribution[name] += current - previous
            previous = current
    scale = 1.0 / len(orders)
    return pd.Series({name: value * scale for name, value in contribution.items()}).sort_values(
        ascending=False
    )

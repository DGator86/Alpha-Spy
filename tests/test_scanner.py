from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from alpha_spy.research.models.physical import TerminalDistribution
from alpha_spy.research.signals.mispricing import ScanSettings, scan_long_options


def test_scanner_finds_deliberately_underpriced_call():
    rng = np.random.default_rng(3)
    now = datetime.now(UTC)
    returns = rng.normal(0.01, 0.02, 100_000)
    prices = 100 * (1 + returns)
    q = TerminalDistribution(returns, prices, 100, 3 / 365, "Q")
    p = TerminalDistribution(returns, prices, 100, 3 / 365, "P")
    model_value = q.expected_payoff(100, "C")
    chain = pd.DataFrame(
        [{
            "symbol": "TEST",
            "timestamp": now,
            "expiration": now + timedelta(days=3),
            "strike": 100.0,
            "right": "C",
            "bid": max(0.01, model_value - 0.55),
            "ask": max(0.02, model_value - 0.50),
            "open_interest": 1000,
            "volume": 100,
        }]
    )
    settings = ScanSettings(
        min_net_edge=0.10,
        min_edge_to_uncertainty=1.0,
        min_probability_positive_edge=0.6,
        max_relative_spread=1.0,
        max_quote_age_ms=1000,
    )
    edges = scan_long_options(
        chain,
        risk_neutral=q,
        physical=p,
        rate=0.0,
        model_uncertainty=0.10,
        settings=settings,
        now=now,
    )
    assert len(edges) == 1
    assert edges[0].net_edge > 0.3

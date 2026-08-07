from __future__ import annotations

import pandas as pd

from spy_constituent_alpha.domain import EdgeEstimate


def edges_to_frame(edges: list[EdgeEstimate]) -> pd.DataFrame:
    rows = []
    for edge in edges:
        row = {
            "symbol": edge.symbol,
            "structure": edge.structure,
            "model_value": edge.model_value,
            "market_entry": edge.market_entry,
            "gross_edge": edge.gross_edge,
            "execution_cost": edge.execution_cost,
            "uncertainty": edge.uncertainty,
            "net_edge": edge.net_edge,
            "edge_to_uncertainty": edge.net_edge / max(edge.uncertainty, 1e-9),
            "probability_positive_edge": edge.probability_positive_edge,
            "max_profit": edge.max_profit,
            "max_loss": edge.max_loss,
            "expected_payoff_physical": edge.expected_payoff_physical,
        }
        row.update(edge.metadata)
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["edge_to_uncertainty", "net_edge"], ascending=False
    ).reset_index(drop=True)

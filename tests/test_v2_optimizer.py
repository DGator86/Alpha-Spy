from alpha_spy.strategy_v2 import V2OptimizerConfig, enumerate_bounded_risk_specs, liquid_contract_pool


def chain():
    rows = []
    for right in ("C", "P"):
        for strike in range(695, 706):
            mid = max(0.30, 1.2 - 0.08 * abs(strike - 700))
            rows.append(
                {
                    "symbol": f"SPY-{right}-{strike}",
                    "expiration": "2026-08-27",
                    "strike": float(strike),
                    "right": right,
                    "bid": round(mid - 0.005, 3),
                    "ask": round(mid + 0.005, 3),
                    "midpoint": mid,
                    "open_interest": 5000,
                    "volume": 500,
                    "iv": 0.18,
                }
            )
    rows.append(
        {
            "symbol": "SPY-C-BAD",
            "expiration": "2026-08-27",
            "strike": 706.0,
            "right": "C",
            "bid": 0.01,
            "ask": 0.08,
            "midpoint": 0.045,
            "open_interest": 5000,
            "volume": 500,
            "iv": 0.18,
        }
    )
    return rows


def test_liquidity_pool_rejects_wide_percentage_spread():
    pool = liquid_contract_pool(chain(), 700.0, V2OptimizerConfig())
    assert "SPY-C-BAD" not in {row["symbol"] for row in pool}


def test_v2_enumerates_simple_and_convex_bounded_risk_families():
    cfg = V2OptimizerConfig(liquid_contracts_per_right=24)
    pool = liquid_contract_pool(chain(), 700.0, cfg)
    specs = enumerate_bounded_risk_specs(pool, 700.0, cfg)
    names = {spec.name for spec in specs}
    assert "LONG_CALL" in names
    assert "LONG_PUT" in names
    assert "CALL_BACKSPREAD_1x2" in names
    assert "PUT_BACKSPREAD_1x3" in names
    assert "IRON_CONDOR" in names or "BROKEN_WING_IRON_CONDOR" in names
    assert all(spec.unique_legs <= 4 for spec in specs)

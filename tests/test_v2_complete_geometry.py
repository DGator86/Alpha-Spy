from alpha_spy.strategy_v2_complete import (
    V2OptimizerConfig,
    enumerate_bounded_risk_specs,
    liquid_contract_pool,
)


ORACLE_FAMILIES = {
    "BEARISH_RISK_REVERSAL_WITH_CALL_WING",
    "BEAR_CALL_CREDIT_SPREAD",
    "BEAR_PUT_DEBIT_SPREAD",
    "BROKEN_WING_IRON_BUTTERFLY",
    "BROKEN_WING_IRON_CONDOR",
    "BULLISH_RISK_REVERSAL_WITH_PUT_WING",
    "BULL_CALL_DEBIT_SPREAD",
    "BULL_PUT_CREDIT_SPREAD",
    "CALL_BACKSPREAD_1x2",
    "CALL_BACKSPREAD_1x3",
    "CALL_BROKEN_WING_BUTTERFLY",
    "CALL_BROKEN_WING_CONDOR",
    "CALL_BUTTERFLY",
    "CALL_CHRISTMAS_TREE",
    "CALL_CONDOR",
    "IRON_BUTTERFLY",
    "IRON_CONDOR",
    "LONG_BOX",
    "LONG_CALL",
    "LONG_GUTS",
    "LONG_PUT",
    "LONG_STRADDLE",
    "LONG_STRANGLE",
    "LONG_STRAP",
    "LONG_STRIP",
    "PUT_BACKSPREAD_1x2",
    "PUT_BACKSPREAD_1x3",
    "PUT_BROKEN_WING_BUTTERFLY",
    "PUT_BROKEN_WING_CONDOR",
    "PUT_BUTTERFLY",
    "PUT_CHRISTMAS_TREE",
    "PUT_CONDOR",
    "REVERSE_BROKEN_WING_IRON_BUTTERFLY",
    "REVERSE_BROKEN_WING_IRON_CONDOR",
    "REVERSE_CALL_BROKEN_WING_BUTTERFLY",
    "REVERSE_CALL_BROKEN_WING_CONDOR",
    "REVERSE_CALL_BUTTERFLY",
    "REVERSE_CALL_CHRISTMAS_TREE",
    "REVERSE_CALL_CONDOR",
    "REVERSE_IRON_BUTTERFLY",
    "REVERSE_IRON_CONDOR",
    "REVERSE_PUT_BROKEN_WING_BUTTERFLY",
    "REVERSE_PUT_BROKEN_WING_CONDOR",
    "REVERSE_PUT_BUTTERFLY",
    "REVERSE_PUT_CHRISTMAS_TREE",
    "REVERSE_PUT_CONDOR",
    "SHORT_BOX",
}


def _chain():
    rows = []
    for right in ("C", "P"):
        for strike in range(688, 713):
            rows.append(
                {
                    "symbol": f"SPY-{right}-{strike}",
                    "expiration": "2026-08-27",
                    "strike": float(strike),
                    "right": right,
                    "bid": 1.99,
                    "ask": 2.01,
                    "midpoint": 2.00,
                    "open_interest": 5000,
                    "volume": 500,
                    "iv": 0.18,
                }
            )
    return rows


def test_complete_v2_matches_47_family_oracle_universe():
    cfg = V2OptimizerConfig(max_strike_distance=20.0, liquid_contracts_per_right=30)
    pool = liquid_contract_pool(_chain(), 700.0, cfg)
    specs = enumerate_bounded_risk_specs(pool, 700.0, cfg)
    names = {spec.name for spec in specs}
    assert names == ORACLE_FAMILIES
    assert all(spec.unique_legs <= 4 for spec in specs)

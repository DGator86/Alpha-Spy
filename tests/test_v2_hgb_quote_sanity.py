from alpha_spy.v2_hgb_vertical_repaired import build_hgb_vertical_candidate, option_quote_sane


def _option(symbol: str, right: str, strike: float, bid: float, ask: float, delta: float):
    return {
        "symbol": symbol,
        "right": right,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "midpoint": 0.5 * (bid + ask),
        "delta": delta,
        "open_interest": 1000,
        "volume": 100,
        "bid_size": 50,
        "ask_size": 50,
        "expiration": "2026-09-02",
        "payload": {},
    }


def _prediction(spot: float = 700.10):
    return {"prediction_id": "P-test", "spy_price": spot}


def _beta():
    return {
        "hgb_direction": {
            "eligible": True,
            "direction": "BULLISH",
            "strength": 0.70,
            "probability_up": 0.75,
            "expected_return_bps": 8.0,
            "core_prediction_bps": 7.0,
            "breadth_prediction_bps": 9.0,
            "model_version": "beta-spy-v2-hgb-trailing-2-exact-target",
        }
    }


def test_below_intrinsic_ask_is_rejected():
    impossible = _option("C700", "C", 700.0, 0.02, 0.05, 0.52)
    assert option_quote_sane(impossible, 700.10) is False


def test_wrong_delta_sign_is_rejected():
    impossible = _option("C700", "C", 700.0, 1.00, 1.02, -0.52)
    assert option_quote_sane(impossible, 700.10) is False


def test_impossible_leg_cannot_enter_hgb_vertical():
    options = [
        _option("C700", "C", 700.0, 0.02, 0.05, 0.52),
        _option("C702", "C", 702.0, 0.01, 0.03, 0.30),
    ]
    assert build_hgb_vertical_candidate(_prediction(), _beta(), options) is None


def test_sane_quotes_preserve_validated_geometry():
    options = [
        _option("C700", "C", 700.0, 1.00, 1.02, 0.52),
        _option("C702", "C", 702.0, 0.44, 0.46, 0.30),
    ]
    candidate = build_hgb_vertical_candidate(_prediction(), _beta(), options)
    assert candidate is not None
    assert candidate["strategy"] == "CALL_DEBIT_SPREAD"

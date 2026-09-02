from alpha_spy.v2_option_sanity import option_price_sane, sane_option_surface


def _option(right="C", strike=100.0, bid=1.00, ask=1.02, delta=0.50):
    return {
        "symbol": f"X-{right}-{strike}",
        "right": right,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "delta": delta,
    }


def test_call_below_intrinsic_ask_is_rejected():
    assert option_price_sane(_option(strike=99.0, bid=0.20, ask=0.30), 100.0) is False


def test_put_below_intrinsic_ask_is_rejected():
    assert option_price_sane(
        _option(right="P", strike=101.0, bid=0.20, ask=0.30, delta=-0.50),
        100.0,
    ) is False


def test_impossible_upper_bound_is_rejected():
    assert option_price_sane(_option(bid=100.00, ask=100.10), 100.0) is False
    assert option_price_sane(
        _option(right="P", strike=90.0, bid=90.00, ask=90.10, delta=-0.50),
        100.0,
    ) is False


def test_invalid_delta_sign_is_rejected_when_delta_is_present():
    assert option_price_sane(_option(delta=-0.50), 100.0) is False
    assert option_price_sane(_option(right="P", delta=0.50), 100.0) is False


def test_missing_delta_does_not_block_generic_price_sanity():
    row = _option(delta=None)
    assert option_price_sane(row, 100.0) is True


def test_surface_filter_removes_only_impossible_quotes():
    good = _option(strike=100.0, bid=1.00, ask=1.02)
    bad = _option(strike=99.0, bid=0.20, ask=0.30)
    assert sane_option_surface([bad, good], 100.0) == [good]

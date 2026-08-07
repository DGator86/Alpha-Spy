import math

from spy_constituent_alpha.pricing.american import american_binomial_price
from spy_constituent_alpha.pricing.black_scholes import black_scholes_price, implied_volatility


def test_implied_volatility_round_trip():
    price = black_scholes_price(100, 105, 0.25, 0.04, 0.01, 0.30, "C")
    recovered = implied_volatility(price, 100, 105, 0.25, 0.04, 0.01, "C")
    assert math.isclose(recovered, 0.30, rel_tol=1e-5)


def test_american_put_not_below_european():
    european = black_scholes_price(100, 110, 0.5, 0.05, 0.0, 0.25, "P")
    american = american_binomial_price(100, 110, 0.5, 0.05, 0.0, 0.25, "P", steps=300)
    assert american >= european - 1e-3

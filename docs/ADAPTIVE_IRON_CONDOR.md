# Adaptive Iron Condor Range and Tail-Loss Model

## Objective

Replace the static iron condor's fixed expected-move fractions with an observable, distribution-driven range model that:

- enters only when SPY implied volatility is rich to both constituent-implied and physical volatility,
- places short strikes at asymmetric physical-distribution quantiles,
- narrows wings as tail risk rises,
- caps expected and conditional tail loss before entry,
- exits intraday when the range, volatility edge, or loss budget invalidates.

The model remains defined risk, intraday only, and requires no stock ownership.

## Observable eligibility

The model is evaluated only from minute 180 through minute 325 of the regular session.

Let:

- `sigma_P` = physical annualized volatility forecast,
- `sigma_Q` = constituent-implied risk-neutral volatility,
- `sigma_M` = SPY market implied volatility,
- `mu_h` = forecast return over the remaining session,
- `B` = weighted five-minute breadth,
- `C` = top-weight contribution concentration.

The principal ratios are:

```text
physical_move = sigma_P * sqrt(T)
directional_ratio = abs(mu_h) / physical_move
Q overpricing = (sigma_M - sigma_Q) / sigma_M
P overpricing = (sigma_M - sigma_P) / sigma_M
```

Entry construction is rejected unless:

```text
Q overpricing >= 5.5%
P overpricing >= 10%
directional_ratio <= 0.38
0.27 <= weighted breadth <= 0.73
tail_risk_score <= 0.92
range_score >= 0.015
```

The range score rewards volatility overpricing and penalizes directional pressure, extreme breadth, and broad low-concentration movement.

## Adaptive central range

The target central probability is:

```text
target_inside = clip(
    0.855
    + 0.065 * tail_risk_score
    + 0.030 * directional_ratio
    + variant_adjustment,
    0.855,
    0.955,
)
```

Two candidates are generated:

- `ADAPTIVE_IRON_CONDOR_BALANCED`
- `ADAPTIVE_IRON_CONDOR_DEFENSIVE`

The defensive candidate adds 4.5 percentage points to the target central probability and uses narrower wings.

## Asymmetric tail allocation

The lower and upper tail probabilities are not forced to be equal. Negative drift and steeper downside skew push the short put farther away; positive drift shifts more room to the call side.

Short strikes are calculated from the physical lognormal terminal distribution:

```text
ln(S_T) ~ Normal(
    ln(S_0) + (mu - 0.5 * sigma_P^2) * T,
    sigma_P^2 * T
)
```

The put short strike is floored to the nearest whole-dollar strike and the call short strike is ceiled. Each must remain at least one strike outside spot.

## Wing and maximum-loss adaptation

Balanced wings default to $2 and may widen to $3 only in low-tail-risk states. Defensive wings default to $1. Both contract to $1 when tail risk is elevated.

A candidate is rejected unless all of the following are satisfied:

```text
credit >= $0.055 per share
maximum loss <= $2.75 per share
credit / maximum loss >= 20%
risk-neutral edge >= $0.010
physical expected edge >= $0.012
edge score >= 0.10
predicted probability of profit >= 76%
central short-strike probability >= 82%
loss probability <= 24%
expected tail loss / maximum loss <= 15%
95% conditional tail loss / maximum loss <= 92%
```

Expected loss and 95% conditional tail loss are evaluated with deterministic Gauss-Hermite integration over the physical terminal distribution.

## Adaptive intraday management

The strategy is checked every five minutes after entry and exits on the first trigger:

1. **Profit target:** approximately 52% of initial credit.
2. **Tail stop:** the smaller of 58% of maximum loss or the larger of 1.2 times credit and 20% of maximum loss.
3. **Soft range breach:** spot approaches the short strike while the trade is losing.
4. **Edge invalidation:** the constituent/SPY volatility overpricing disappears after at least 20 minutes.
5. **Scheduled exit:** no later than 3:55 PM.

No position is held overnight or allowed to convert into stock exposure.

## Three-sample evidence

| Sample | Static condor P&L | Balanced adaptive P&L | Balanced PF | Balanced worst trade |
|---|---:|---:|---:|---:|
| Development 1,000 worlds | -$24,481.13 | +$259.48 | 3.08 | -$37.47 |
| Validation 1,000 worlds | -$26,726.50 | +$58.80 | 1.23 | -$121.36 |
| Evaluation 1,000 worlds | -$20,087.48 | +$94.09 | 1.75 | -$21.25 |

Only 17 adaptive-condor trades were selected into the final third-sample portfolio, contributing $37.45. The model is therefore promoted as a low-frequency secondary premium-selling sleeve, not as the primary source of expectancy.

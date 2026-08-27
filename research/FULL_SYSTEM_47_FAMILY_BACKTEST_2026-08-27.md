# Full V2 47-Family Allocator Backtest — 2026-08-27

## Status

**Evidence-weighted full system: profitable in the historical causal replay. Unrestricted full allocator: fails. Do not infer live profitability until actual-chain forward validation.**

This test was run after the HGB 15-minute constituent-breadth signal had already been identified. The directional signal timestamps are causal first-cross signals (one trade maximum per day; no best-time hindsight). Historical option chains were not recorded for this period, so option values/spreads remain synthetic. The forward V2 chain recorder is intended to remove that limitation going forward.

## Architecture under test

All 47 bounded-risk families remain available. The full allocator includes:

- HGB directional lane.
- Expansion/long-vol lane.
- Quiet/range/premium lane.
- Full P/Q dislocation lane.
- NO_TRADE.

Two allocation policies were compared:

1. **Unrestricted** — any eligible family may immediately win the trade.
2. **Evidence-weighted champion/challenger** — all families are evaluated, but the validated ATM 2-point directional debit vertical is the champion on a qualified HGB signal. A challenger must clear a material ex-ante improvement in predicted EV, probability of profit, robust score, and execution drag before it may replace the champion. Quiet/expansion/P-Q lanes remain eligible under their own higher evidence thresholds.

## Main results

| Allocator | IV stress | Trades | Net P&L | Win rate | Profit factor | Max drawdown | First half | Second half |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Evidence-weighted full system | 13% | 18 | **+$180.48** | 72.2% | 5.11 | -$19.22 | +$94.52 | +$85.97 |
| Evidence-weighted full system | 20% | 18 | **+$112.26** | 72.2% | 4.29 | -$14.19 | +$58.66 | +$53.60 |
| Evidence-weighted full system | 30% | 18 | **+$66.57** | 72.2% | 3.39 | -$10.53 | +$33.98 | +$32.59 |
| Unrestricted full system | 13% | 11 | +$16.38 | 54.5% | 1.24 | -$52.72 | +$2.70 | +$13.68 |
| Unrestricted full system | 20% | 11 | +$5.22 | 54.5% | 1.07 | -$55.68 | +$13.58 | -$8.36 |
| Unrestricted full system | 30% | 11 | **-$9.21** | 54.5% | 0.89 | -$59.02 | +$19.18 | -$28.39 |

Exact day-level sign-flip tests on the fixed trade P&L:

- Evidence-weighted 13% IV: p ≈ **0.0064**.
- Evidence-weighted 20% IV: p ≈ **0.0109**.
- Evidence-weighted 30% IV: p ≈ **0.0227**.
- Unrestricted 20% IV: p ≈ **0.466**.

These are individual fixed-system tests and are not a substitute for forward validation.

## Family attribution — 20% IV

### Unrestricted allocator

The unrestricted system actually exercised multiple strategy families:

- IRON_BUTTERFLY: 2 trades, +$20.07.
- LONG_CALL: 4 trades, +$2.87.
- BEAR_PUT_DEBIT_SPREAD: 2 trades, -$2.67.
- BULL_CALL_DEBIT_SPREAD: 2 trades, -$6.44.
- LONG_PUT: 1 trade, -$8.60.

Total: **+$5.22**, with a **-$55.68** maximum drawdown.

The additional freedom did not create robust alpha.

### Evidence-weighted allocator

- HGB champion/control lane: 17 trades, **+$115.33**.
- Full-47 challenger lane: 1 trade, **-$3.07**.

Family mix:

- BULL_CALL_DEBIT_SPREAD: 8 trades, +$75.42.
- BEAR_PUT_DEBIT_SPREAD: 9 trades, +$39.91.
- LONG_CALL challenger: 1 trade, -$3.07.

The only challenger replacement occurred on 2026-08-05. It reduced P&L by about **$1.17** versus retaining the 2-point control vertical.

## Interpretation

This is not evidence that the system should contain only one strategy. It is evidence that **strategy diversity must be earned by incremental predictive/execution value**.

All 47 bounded-risk families remain in the candidate universe. The current data says that the HGB directional signal is the source of the demonstrated edge, while unrestricted payoff-family selection mostly destroys it. The full system is therefore best represented as a champion/challenger allocator: the validated low-friction directional expression is the default expression of a qualified directional signal, while every other family continuously competes for authority and is promoted only when its forward evidence demonstrates incremental value.

Quiet/expansion/P-Q lanes remain active research lanes rather than deleted families. Their historical monetization evidence is weaker and therefore their execution thresholds are higher.

## Decision

Preserve the **full 47-family architecture**, but do not permit unrestricted family selection to override the validated directional signal.

Next forward sessions must record the complete actual 0DTE SPY chain, exact candidate rankings, order-preview costs, chosen trade, shadow alternatives, T+15 marks, and realized fills. The forward comparison should explicitly measure incremental P&L/regret of every challenger against the champion so additional strategy families can earn execution authority from actual market data rather than synthetic historical option surfaces.

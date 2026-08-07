# Failure Modes

1. **Circular valuation** — using SPY option prices to calibrate dependence and then testing those same prices.
2. **Risk-premium confusion** — treating persistent crash/correlation compensation as arbitrage.
3. **Asynchronous marks** — creating fake basket and volatility gaps from mismatched timestamps.
4. **Survivorship bias** — using current S&P 500 members in historical tests.
5. **Coverage renormalization** — treating incomplete constituent-option coverage as the full index.
6. **Earnings contamination** — interpreting scheduled idiosyncratic event variance as market information.
7. **Borrow contamination** — interpreting expensive-to-borrow put/call differences as informed direction.
8. **Last-price fantasy** — valuing and filling trades at stale last prints.
9. **Midpoint execution fantasy** — assuming the model receives both legs at midpoint.
10. **Latency leakage** — allowing a feature timestamp that becomes observable after the option quote used for entry.
11. **Unstable lag mining** — selecting the best lag in-sample without nested walk-forward validation.
12. **Tail under-modeling** — simulating Gaussian dependence and selling options against crisis correlation.
13. **SPX/SPY mismatch** — ignoring exercise style, settlement, dividends, and basis.
14. **One-regime alpha** — a strategy whose profits come only from March 2020 or another isolated episode.
15. **Capacity blindness** — selecting edges in contracts too wide or shallow to execute.

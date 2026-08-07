# Professional Intraday Risk and Exit System

## Operating doctrine

The system is not optimized for maximum win rate. It seeks positive tail-adjusted expectancy, favorable payoff asymmetry, and controlled intraday drawdown. Every position is finite-risk, contains no shares, and is closed before the regular session ends.

## Universal controls

- Entry decisions: five-minute grid.
- Exit monitoring: every minute.
- Maximum one position per simulated universe.
- Maximum modeled loss: $900.
- No naked options, stock ownership, or overnight exposure.
- Weak positions are reduced at minute 378; all positions are closed by minute 385.
- Exit calculations use executable bid/ask pricing, fees, and slippage.
- Exit decisions use only data available at the decision minute.

## Long calls, long puts, and debit verticals

- Dynamic debit stop tightens as time expires: approximately 50%, 42%, then 33% of debit.
- Capped verticals exit near 78% of modeled maximum profit.
- Profit trail activates after a 35% return on debit.
- Trail tightens after gains exceed 125% of debit.
- Exit when constituent direction and breadth reverse for multiple observations.
- Exit when long-premium relative-value edge disappears and the position has not progressed.
- Exit after the expected directional move is substantially complete and the forecast decays.
- Time stop applies when the trade has not produced meaningful favorable excursion.

## Long straddles, strangles, reverse irons, and backspreads

- Dynamic risk stop tightens from roughly 48% to 32% of debit.
- Capped convex structures exit near 80% of maximum profit.
- Convex trailing exit activates after a 50% gain on debit.
- The trail tightens materially after a 150% gain.
- Exit when constituent-implied volatility advantage disappears and neither movement nor direction supports the position.
- Exit when the realized move exceeds the expected move and volatility no longer supports continued holding.
- Time stop prevents continued theta exposure after insufficient favorable excursion.

## Directional credit spreads

- Profit target adapts from approximately 38% to 52% of credit.
- Tail stop is bounded by both credit received and maximum spread loss.
- Exit before the short strike is reached when the price enters a 25%-of-wing threat buffer.
- Directional invalidation requires persistent forecast and breadth reversal, not one noisy observation.
- Relative-volatility invalidation requires persistence and will not turn a winning trade into an avoidable loss.
- A profit trail protects captured credit.
- A time stop removes stalled trades after most of the planned holding window has elapsed.

## Iron condors and iron butterflies

- Profit target adapts from approximately 45% to 58% of credit.
- Tail loss is capped near 24% of maximum defined loss or a tighter credit-based threshold.
- Each short strike has an adaptive 30%-of-wing boundary buffer.
- Boundary threats must persist before forcing an exit.
- The volatility-sale thesis must remain valid; otherwise profitable positions are closed.
- Captured credit is protected with a trailing exit.

## Debit butterflies and debit condors

- Stop near 42% of debit.
- Profit target near 58% of maximum modeled profit.
- Exit if price leaves the structure's payoff tent while the trade is losing.
- Time stop applies when price has failed to migrate toward the payoff zone.
- Favorable excursion is protected with a trailing exit.

## Broken-wing butterflies and other conditional structures

- Maximum loss remains defined at entry.
- Risk stop is based on the actual debit or bounded maximum loss.
- Directional variants exit on persistent thesis reversal.
- Capped structures harvest approximately 65% of maximum profit.
- Favorable excursion is protected with a trailing exit.

## Model-selection controls

The selector uses an ensemble rather than a single probability estimate. It conservatively estimates:

- win probability;
- expected winner size;
- expected loss size;
- conditional 90th-percentile loss;
- maximum favorable excursion;
- maximum adverse excursion.

A high win probability is not sufficient. The candidate must also clear predicted profit-factor, expected-value, tail-loss, and excursion-efficiency gates.

## Remaining limitations

This remains a historically anchored simulation. It is not a point-in-time replay of actual SPY, SPX, ES, constituent, and option-market quotes. Live deployment remains prohibited until synchronized historical replay, paper execution, and operational risk testing are complete.

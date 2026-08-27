# Alpha-SPY / Beta-SPY V2 RC1 Freeze

Freeze ID: `alpha-beta-v2.0.0-rc1`

This file defines the Alpha side of the next untouched forward test. Do not tune these values from August 18-26 or from the August 27 outcome.

## Authority

- Beta V2 supplies validated market-state probabilities/trust only.
- Beta has no strategy authority.
- Alpha V2 is the sole payoff-geometry, trade/no-trade, risk and execution authority.
- Missing/stale/untrusted Beta V2 state fails closed to `NO_TRADE`.

## Existing Alpha hardening remains authoritative

V2 preserves verified market/option inputs, surface coverage, P/Q coverage, model uncertainty vetoes, event/context blocks, broker reconciliation, five-minute entry anchors, one-contract fail-closed scope, <= $100 modeled trade risk, position management, forecast-horizon exit and forced-flat controls.

Current configured entry window ends at 15:40 ET and forced flat remains 15:55 ET, allowing a complete 15-minute primary horizon at the final entry anchor.

## Liquidity-first option pool

For broad payoff generation, an option must satisfy:

- absolute bid/ask spread <= $0.05;
- relative spread <= 25% of midpoint;
- open interest >= 10;
- volume >= 0 (newly opened 0DTE strikes are not rejected solely for zero printed volume);
- strike inside a dynamic distance from spot of 8 to 18 dollars, based on Beta's expected absolute move;
- at most the top 28 liquidity-ranked calls and top 28 puts proceed to geometry generation.

Liquidity ranking heavily penalizes absolute/relative spread and rewards quoted bid/ask depth, open interest, volume and proximity to spot. Tradier `bidsize` / `asksize` preserved in the raw option payload are restored when DB columns do not directly expose quote depth.

## Payoff tournament

V2 generates broad bounded-risk geometry across:

- outright calls/puts;
- debit and credit verticals;
- straddles, strangles, guts, straps and strips;
- symmetric/broken-wing butterflies and reverse flies;
- Christmas trees and reverse Christmas trees;
- 1x2 and 1x3 call/put backspreads;
- same-right condors/broken-wing condors and reverses;
- iron condors, iron butterflies, broken-wing versions and reverses;
- winged bullish/bearish risk reversals;
- long/short box controls.

The generator is not limited by the legacy 40-candidate choke point. It pre-sorts by quoted execution drag and complexity, then performs full P/Q repricing on at most 1,800 structures and retains at most 300 scored candidates.

## Economic gates

A V2 candidate must satisfy all of:

- expected P&L >= $3 after modeled execution costs;
- probability of profit >= 0.58;
- edge-to-uncertainty >= the configured threshold (currently 0.75);
- modeled maximum loss > $0 and <= $100;
- doubled-cost expected value > $0.

Beta state adjusts candidate scores as a probabilistic prior; it does not hard-ban the opposite direction or prescribe a strategy family.

## Cost truth

- Broad ranking uses executable option bid/ask prices and quoted spread drag.
- Broad V2 valuation does not impose the legacy blanket $0.65/contract/side commission.
- Minimum synthetic slippage is zero; residual modeled slippage is capped at 10% of the quoted spread for the broad tournament.
- Up to the top eight eligible finalists are submitted to Tradier order preview when credentials are available.
- Broker-returned commission + fees are reserved for both entry and exit and can veto a finalist if they remove its edge.
- Actual broker fills/fees remain authoritative after execution.

## Exact replay evidence

For every V2 decision Alpha persists the exact option chain used for selection, including strikes, bid/ask, bid/ask size, OI, volume, IV and Greeks, and binds it to the prediction with SHA-256.

The Beta session-tape archiver now includes:

- exact decision-linked V2 option-chain records;
- Alpha raw SPY option-chain JSONL when present;
- Alpha DB fallback SPY strategy chains/quotes;
- V2 candidate records;
- Beta V2 market-state records.

Therefore future V2 replay is designed to use real historical option quotes rather than reconstructing a synthetic chain.

## Forward-test rule

August 18-26 are burned development/diagnostic sessions for RC1. August 27 is the next untouched session. The RC1 configuration, family universe, scoring and gates must remain unchanged until the August 27 decisions are locked and their outcomes mature.
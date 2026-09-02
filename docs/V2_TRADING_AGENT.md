# V2 Closed-Loop Trading Agent

This document is the governing architecture for Alpha-SPY V2. Models, indicators,
playbooks, option structures, execution rules, management rules and learning logic
must fit inside this chain. No component may bypass the chain by turning a raw
signal or nominal positive expected value directly into a trade.

## Canonical loop

**Regime → Lifecycle Forecast → Edge → Playbook → Timing → Instrument → Economics → Execution → Monitoring → Exit → Attribution → Learning → Governance → Regime**

The implementation is an orchestrator around specialized engines, not one monolithic
AI trader.

## Authority map

- **Alpha hierarchical regime engine — Steps 1-2.** Alpha is the only authoritative
  market-regime classifier. It combines volatility, correlation, breadth,
  concentration, dealer-gamma proxy, session, event state, risk tone, volatility
  term structure and liquidity across micro, intraday, swing and structural
  horizons, including cross-horizon conflict.
- **Alpha Regime Lifecycle Engine — Steps 3-4.** It estimates remaining regime life,
  survival/hazard and successor-regime probabilities from already-completed Alpha
  regime episodes. Beta variables may weight historical analogs but cannot define
  the regime or its target outcome.
- **Beta-SPY — independent witness.** Beta supplies causal constituent/tape evidence,
  HGB direction, breadth/state distribution and its own non-authoritative regime
  view for confirmation/disagreement diagnostics. `strategy_authority=false` and
  `regime_authority=false` are invariants.
- **Opportunity/Alpha trade-design layer — Steps 5-9.** It combines Alpha regime,
  lifecycle, Beta witness and Alpha P/Q/option economics to decide whether an edge
  exists and how to express it.
- **Alpha execution — Step 10.** Exact current-chain instrument, preview, limit/fill,
  partial fill and reconciliation.
- **Alpha closed-loop settlement — Steps 11-13.** It continuously reassesses the
  thesis and chooses HOLD / ADD / SCALE / TAKE_PROFIT / BAIL / SELL_FOR_LOSS /
  ADJUST / RESTRUCTURE.
- **Outcome attribution — Step 14.** Process components are scored independently;
  unsupported questions remain UNKNOWN.
- **Meta / Playbook governance — Steps 15-16.** Component-specific errors become
  research challengers; playbooks are promoted/narrowed/retired only with repeated
  closed-trade evidence.

## 1. Is the market in a definable regime?

Alpha first builds its hierarchical regime. The lifecycle layer then asks whether
there is enough completed historical episode support to make an actionable lifecycle
forecast.

- Undefined/ambiguous Alpha regime or insufficient lifecycle support → `NO_TRADE`.
- Definable regime with sufficiently calibrated lifecycle support → continue.

Regime recognition is never trade permission.

## 2. What regime is it?

Alpha compresses the hierarchical regime into the actionable lifecycle alphabet:

- `QUIET`
- `DIRECTIONAL_UP`
- `DIRECTIONAL_DOWN`
- `EXPANSION`
- `TRANSITION`

The underlying hierarchy remains attached to every decision so the compact label
does not discard volatility, breadth, gamma, event, liquidity or cross-horizon
context. Beta may disagree; that disagreement is evidence, not authority.

## 3. How long is the regime expected to last?

The Alpha lifecycle engine stores contiguous same-session Alpha regime episodes.
Only completed prior episodes become training outcomes; the current episode remains
right-censored.

For the current regime and current regime age it estimates:

- `P(survive 5m)` / `P(survive 15m)` / `P(survive 30m)`;
- discrete hazard over 0-5, 5-15 and 15-30 minutes;
- expected remaining regime life;
- p10 / p25 / p50 / p75 / p90 remaining-duration quantiles;
- effective matched-episode support;
- calibration from already-matured lifecycle forecasts.

When completed-episode support is insufficient the engine may emit a provisional
Alpha+Beta-witness fallback for observation and replay, but it explicitly marks the
forecast non-definable for trading authority. Cold start cannot be bypassed merely
because another signal is strong.

## 4. If it ends, where does it go next?

Successor probabilities are learned from the first observed successor of completed
Alpha regime episodes conditioned on the current state/age. Beta direction,
large-move/reversal/persistence evidence may affect analog similarity, but Beta does
not supply the successor target.

Every lifecycle forecast records:

- normalized successor-regime probabilities;
- most likely successor;
- successor confidence;
- source (`EMPIRICAL...` vs provisional fallback);
- matched/effective episode count.

Matured forecasts are scored against actual subsequent Alpha observations for
survival Brier score, duration error and successor accuracy.

## 5. Is there a monetizable edge?

The opportunity layer evaluates both the current regime and the expected transition.
Inputs include:

- Alpha regime hierarchy;
- Alpha lifecycle survival/transition probabilities;
- Beta HGB and constituent-state witness;
- Alpha physical P / observed option-implied Q;
- historical playbook action value;
- uncertainty and implementation costs.

No identifiable robust edge → `NO_TRADE`. A positive theoretical option EV alone
is not an edge.

A material conflict can produce `WAIT`. Example: Beta HGB is bullish while Alpha's
high-confidence successor is `DIRECTIONAL_DOWN`; the system waits for resolution
instead of forcing a directional trade.

## 6. Which playbook best monetizes the edge?

Playbooks sit above option structures. Initial research playbooks include:

- `DIRECTIONAL_MOMENTUM`
- `LATE_RANGE_CARRY`
- `VOLATILITY_EXPANSION`
- `MEAN_REVERSION`
- `REGIME_TRANSITION`
- `P_Q_RELATIVE_VALUE`

`NO_EDGE` / `NO_TRADE` competes against every playbook. Step-16 governance may block
a playbook that has earned `NARROW_OR_RETIRE` status even when a fresh candidate
looks attractive.

## 7. When should the playbook enter?

A valid edge may yield `WAIT`, not only `ENTER`.

Entry modes include:

- `EXECUTE_NOW`
- `WAIT_FOR_CONFIRMATION`
- `WAIT_FOR_BETTER_PRICING`
- `WAIT_FOR_TRANSITION`

A waiting thesis persists across decision cycles with explicit expiry and
invalidation. When the trigger arrives Alpha rebuilds from the current market and
option chain; stale strikes are never executed. If the thesis deteriorates or
expires, it is cancelled.

Repeated entries into the same regime/setup episode are suppressed. A subsequent
trade requires a genuinely new setup episode.

## 8. Which instrument and exact option structure express it?

Only after Steps 1-7 does Alpha choose the expression. The V2 optimizer can value
the complete 47-family same-expiration bounded-risk universe captured by the
research oracle. Selection considers:

- direction / payoff geometry;
- expiration, strikes and widths;
- delta/gamma/theta/vega;
- IV and skew;
- OI, volume and displayed depth;
- bid/ask spread;
- expected payoff under physical P;
- observed market-implied Q;
- path and transition fit.

The structure is subordinate to the market thesis.

## 9. Is implementation economically tolerable?

The trade must survive premium, spread, slippage, fees, theta/vega risk, collateral,
opportunity cost, maximum loss, uncertainty and execution drag. V2 deliberately
stresses quote drag rather than assuming midpoint fills. Marginal trades wait,
redesign or disappear.

## 10. Execute and freeze a TradeThesis

Immediately before entry Alpha rechecks the regime/lifecycle, trigger, quotes, risk
and economics. The selected candidate receives a frozen `TradeThesis` containing:

- Alpha regime hierarchy and compact regime;
- lifecycle forecast ID and full survival/transition forecast;
- Beta witness state;
- edge source / playbook;
- strategy / direction;
- entry mode, trigger and setup expiration;
- market state / IV at setup and entry;
- expected time-to-profit;
- profit targets, economic stop and time stop;
- invalidation / adjustment / scaling rules;
- implementation economics;
- risk budget and maximum scale capacity;
- playbook-governance status.

The initial position starts small. Additional risk must be earned by strengthening
evidence and must remain inside the frozen risk budget; the agent never averages
down simply because a trade is losing.

The V2 branch remains research/paper only until actual-chain forward validation
supports promotion.

## 11. Monitor continuously

The settlement loop re-evaluates the open thesis every minute using:

- current **Alpha** regime/lifecycle state;
- lifecycle persistence/successor changes;
- Beta HGB and constituent-state witness;
- fair combo value and executable liquidation value;
- P&L, MFE and elapsed time;
- live IV of held legs;
- remaining time / opportunity cost.

The central question is: **Is the original thesis still valid and is it progressing
on schedule?**

Open-position management reads the same Alpha lifecycle authority used at entry. If
that state is missing/stale it becomes explicitly undefined; settlement never
silently promotes Beta's regime view to authority.

Fair-value management and liquidation economics remain separate so multi-leg bid/ask
width does not manufacture a false stop.

## 12. Evaluate the exit plan

The manager checks:

- first/second profit targets;
- economic stop;
- thesis invalidation;
- Alpha regime/lifecycle transition;
- successor-regime change;
- Beta HGB flip;
- IV shock;
- expected-time-to-profit failure;
- playbook-specific hard time stop;
- post-target giveback;
- opportunity to adjust/restructure.

There is no universal T+15 exit. Timing is thesis/playbook specific.

## 13. Take a position action

The state machine can emit:

- `HOLD`
- `ADD`
- `SCALE`
- `TAKE_PROFIT`
- `BAIL`
- `SELL_FOR_LOSS`
- `ADJUST`
- `RESTRUCTURE`

`ADD` requires strengthening evidence, nonnegative trade economics, remaining risk
capacity and an intact thesis. `SCALE` can lock profit after target 1. `ADJUST` /
`RESTRUCTURE` protect or close the old expression and re-arm Alpha to build the best
current expression rather than defend stale geometry.

## 14. Did it work, and why?

After closure the system does not equate profit with good process. Attribution is
component-specific:

- **Regime identification:** retained as `UNKNOWN` unless an independent truth label
  exists; the system does not grade its own latent label as correct merely because
  it produced one.
- **Duration:** compared with matured episode duration / 30m censoring.
- **Transition:** compared with the first actually observed successor regime.
- **Edge quality:** a single P&L result cannot prove/disprove edge, so this remains
  `UNKNOWN` until repeated samples are evaluated.
- **Strategy / option structure:** scored only when same-prediction shadow candidate
  outcomes exist; regret versus the best bounded-risk counterfactual is recorded.
- **Entry timing:** remains `UNKNOWN` until nearby-state replay supplies a genuine
  timing counterfactual.
- **Cost assumptions:** modeled execution drag is compared with actual fill slippage
  when actual fills are available.
- **Risk management:** checks bounded loss and predefined invalidation.
- **Exit quality:** evaluates realized P&L capture relative to MFE when measurable.

Unknown evidence is not counted as a pass.

## 15. What was learned?

Step 15 feeds the component that failed; it does **not** globally mutate the trader.
Examples:

- duration error → lifecycle survival model research;
- successor error → transition model research;
- strategy regret → playbook/strategy-selection research;
- timing regret → entry-trigger research;
- cost miss → execution model;
- poor MFE capture → management/exit research.

Closed reviews are refreshed after lifecycle/candidate outcomes mature. Component
failures create challengers or narrower hypotheses; they never rewrite live rules
from one observation.

## 16. Will the setup be used again?

The governance engine reads closed trades only and evaluates sample count, realized
action value, win rate, profit factor, drawdown, process score and lifecycle-error
rate. Research statuses are:

- `EXPERIMENTAL` — fewer than 8 independent closed examples;
- `CHALLENGER` — evidence exists but is below repeatability threshold;
- `PROVISIONAL_REPEATABLE` — 20+ samples with positive record/process quality but
  more forward evidence required;
- `VALIDATED_PLAYBOOK` — 40+ samples with positive, sufficiently robust record;
- `NARROW_OR_RETIRE` — realized action value/process/lifecycle behavior is inadequate.

Tiny samples cannot promote a playbook regardless of headline P&L. A single result
never mutates authority.

After review the system returns to Step 1.

## Non-negotiable invariants

1. Alpha hierarchical regime owns Steps 1-2.
2. Alpha lifecycle owns Steps 3-4; provisional fallback is not trading authority.
3. Beta is an independent witness and has neither regime nor strategy authority.
4. Undefined/unsupported lifecycle cannot trade.
5. Positive nominal EV alone cannot trade.
6. Waiting setups persist, expire and invalidate explicitly.
7. Exact instruments are rebuilt from current quotes at execution time.
8. Entry economics must survive stressed execution drag.
9. No averaging down: scale-in requires stronger evidence and remaining risk budget.
10. Fair combo value and executable liquidation value are separate.
11. Exit timing is playbook/thesis specific.
12. Unsupported post-trade questions remain UNKNOWN, never fake PASS.
13. Errors feed the component that failed, not a global self-modifying model.
14. One trade cannot promote or retire a playbook.
15. `NO_TRADE` remains first-class at every pre-entry stage.

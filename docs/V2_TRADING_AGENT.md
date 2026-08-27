# V2 Closed-Loop Trading Agent

This document is the governing architecture for Alpha-SPY V2. Any model, indicator,
playbook, strategy, option structure, execution rule, position-management rule, or
learning process must fit inside this chain. No component may bypass the chain by
turning a raw signal or nominal positive expected value directly into a trade.

## Canonical loop

**Regime → Forecast → Edge → Strategy → Timing → Instrument → Economics → Execution → Monitoring → Exit → Evaluation → Learning → Repeatability → Regime**

## 1. Is the market in a definable regime?

Beta must first decide whether the current market environment has sufficient causal
state support to be classified. The decision includes analog support, model
confidence, calibration/conformal uncertainty, and data health.

- Undefined or insufficiently supported regime → `NO_TRADE`.
- Definable regime → continue.

Regime recognition is not trade permission.

## 2. What regime is it?

Beta classifies the current state. The initial vocabulary includes:

- `QUIET`
- `DIRECTIONAL_UP`
- `DIRECTIONAL_DOWN`
- `EXPANSION`
- `TRANSITION`

The vocabulary may evolve only through validated research. Every regime output must
include confidence rather than only a label.

## 3. How long is the regime expected to last?

Beta publishes:

- 15-minute persistence probability;
- 30-minute persistence probability;
- expected actionable duration;
- uncertainty / confidence.

If expected duration is too short or uncertain to monetize, Alpha stands aside or
waits for a transition rather than forcing a structure into the current state.

## 4. If it ends, where does the market go next?

Beta publishes a normalized probability distribution over successor regimes, plus
the most likely successor and its confidence. Alpha may trade the current regime or
wait for the forecast transition if the transition is the actual edge.

## 5. Is there a monetizable edge?

Alpha evaluates both:

1. economics of the current regime; and
2. economics of the expected transition.

No identifiable edge after uncertainty and implementation costs → `NO_TRADE`.
Positive theoretical option EV by itself is not an edge.

## 6. Which playbook best monetizes the edge?

Playbooks are higher-level than option structures. Initial V2 playbooks include:

- `DIRECTIONAL_MOMENTUM`
- `LATE_RANGE_CARRY`
- `VOLATILITY_EXPANSION`
- `MEAN_REVERSION`
- `REGIME_TRANSITION`
- `P_Q_RELATIVE_VALUE`

Additional trend, relative-value, carry, volatility, and transition-specific
playbooks may be promoted only after independent evidence.

`NO_EDGE` / `NO_TRADE` competes against every playbook.

## 7. When should the playbook enter?

A valid edge can produce `WAIT`, not only `ENTER`.

Entry modes include:

- `EXECUTE_NOW`
- `WAIT_FOR_CONFIRMATION`
- `WAIT_FOR_BETTER_PRICING`
- `WAIT_FOR_TRANSITION`

A waiting thesis is persisted across decision cycles. It has a setup expiry and
explicit invalidation rules. If its trigger arrives, Alpha rebuilds the trade from
the current market and option chain. It never executes stale strikes from the old
setup. If the thesis deteriorates or expires, it is abandoned.

Repeated entries into the same regime/setup episode are suppressed. A new trade
requires a genuinely new setup episode.

## 8. Which instrument and exact option structure express it?

Only after Steps 1-7 does Alpha evaluate the instrument. The current V2 optimizer can
value the complete 47-family same-expiration bounded-risk universe captured by the
research oracle. Instrument selection considers:

- direction and payoff geometry;
- expiration;
- strikes and widths;
- Greeks;
- IV and skew;
- OI, volume, displayed depth;
- bid/ask spread;
- expected payoff under physical P;
- market-implied Q;
- path/transition fit.

The structure is subordinate to the market thesis.

## 9. Is implementation economically tolerable?

The trade must survive premium, spread, slippage, fees, theta/vega risk, collateral,
opportunity cost, maximum loss, uncertainty, and execution drag.

V2 uses an intentionally harsh execution test: candidate edge is evaluated after a
multiple of estimated quote drag rather than assuming midpoint fills. Marginal
trades wait, redesign, or disappear.

## 10. Execute and freeze a TradeThesis

Immediately before entry Alpha rechecks the regime, trigger, option quotes, risk,
and economics. The selected candidate receives a frozen `TradeThesis` recording:

- regime and confidence;
- persistence and duration;
- successor probabilities;
- edge source and playbook;
- strategy and direction;
- entry mode / trigger / setup expiration;
- market state and IV at entry;
- expected time to profit;
- first and second targets;
- economic stop;
- time stop;
- invalidation rules;
- adjustment / scaling rules;
- implementation economics;
- risk budget and maximum scale capacity;
- evidence status / playbook history.

The initial position starts small. Additional exposure has to be earned by stronger
subsequent evidence and must remain inside the frozen risk budget. The agent never
adds simply because a position is losing.

The V2 branch remains paper/sandbox-only. Autonomous scale-in to external broker
exposure is fail-closed; paper mode exercises the full sizing state machine.

## 11. Monitor continuously

The settlement loop re-evaluates the open thesis every minute using:

- current regime and confidence;
- persistence and successor probabilities;
- HGB direction;
- predicted large-move probability;
- fair combo value;
- executable liquidation value;
- P&L, MFE and time elapsed;
- live IV of the held structure;
- remaining time / opportunity cost.

The central question is: **Is the original thesis still valid and is it progressing
on schedule?**

Fair combo economics and liquidation economics are deliberately separated. A
multi-leg trade is not stopped merely because immediately crossing every spread
would print a temporary loss.

## 12. Evaluate the exit plan

The manager checks:

- first and second profit targets;
- economic stop;
- thesis invalidation;
- regime transition;
- successor-regime change;
- HGB flip;
- IV shock;
- expected-time-to-profit failure;
- hard playbook time stop;
- post-target profit giveback;
- opportunity to adjust/restructure.

There is no universal T+15 exit. Time expectations are playbook-specific.

## 13. Take a position action

The management state machine can emit:

- `HOLD`
- `ADD`
- `SCALE`
- `TAKE_PROFIT`
- `BAIL`
- `SELL_FOR_LOSS`
- `ADJUST`
- `RESTRUCTURE`

`ADD` requires strengthening evidence, nonnegative trade economics, remaining risk
capacity, and intact thesis. `SCALE` can lock profit after the first target.
`ADJUST` / `RESTRUCTURE` protects or closes the old expression and re-arms the
engine to rebuild the best current expression rather than keeping stale geometry.

## 14. Did the trade work, and why?

After closure, V2 does not equate profit with good process. It separately scores:

- regime identification;
- duration forecast;
- transition forecast;
- edge quality;
- strategy selection;
- entry timing;
- cost assumptions;
- risk management;
- exit execution.

A trade is attributed as good/bad process crossed with favorable/unfavorable
outcome/variance.

## 15. What was learned?

Each closed trader-agent position stores a post-trade review and explicit lessons.
The system tracks realized playbook history using only already-closed positions.
Open positions and future outcomes never enter the learning set.

## 16. Will the setup be used again?

A single result never mutates the live playbook. Evidence states progress through:

- `EXPERIMENTAL`
- `PROVISIONAL`
- `RESEARCH_VALIDATED_FORWARD_PENDING`
- `REPEATABLE`
- `NARROW_OR_RETIRE`

Lessons must recur independently before a rule is promoted, narrowed, or retired.
Failure modes can become explicit avoidance rules only after repeatability testing.

After review, the entire system returns to Step 1 and observes the current regime
again.

## Component responsibility

### Beta-SPY

Beta is strategy-agnostic intelligence. It owns causal market-state estimation,
regime definition, regime persistence/duration, successor-regime probabilities,
HGB directional evidence, magnitude/volatility context, and empirical analog
outcomes. `strategy_authority` must remain false.

### Alpha-SPY

Alpha owns edge determination, playbook selection, entry timing, state-conditioned
P versus option-implied Q valuation, exact structure selection, execution economics,
trade-thesis creation, monitoring, management, attribution, and playbook governance.

## Non-negotiable invariants

1. Undefined regime cannot trade.
2. Beta never chooses an option strategy.
3. Positive nominal EV alone cannot trade.
4. Waiting setups persist, expire, and invalidate explicitly.
5. Exact instruments are selected from current quotes at execution time.
6. Entry economics must survive stressed execution drag.
7. No averaging down: scale-in requires stronger evidence and remaining risk budget.
8. Fair combo value and executable liquidation value are separate concepts.
9. Exit timing is playbook/thesis specific, not universally fixed.
10. Every closed trade receives process attribution.
11. One trade cannot rewrite the system; learning requires independent recurrence.
12. `NO_TRADE` remains a first-class decision at every pre-entry stage.

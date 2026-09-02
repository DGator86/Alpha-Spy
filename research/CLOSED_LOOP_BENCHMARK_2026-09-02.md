# Closed-Loop Alpha/Beta Benchmark — 2026-09-02

## Scope

This is the point-in-time benchmark for the 16-step closed-loop SPY options architecture. It records what is on `main`, what exists only on research branches, what has been falsified, and what remains non-authoritative. This document is research/paper governance only. It does not authorize real-money execution.

Canonical chain:

`Regime -> Lifecycle -> Edge -> Playbook -> Timing -> Instrument -> Economics -> Execution -> Monitor -> Exit -> Attribution -> Learning -> Governance -> Regime`

## Repository snapshot

| Repository / branch | Benchmark head | Role |
|---|---|---|
| Alpha-SPY `main` | `4cc4907a87792a4f3f2a8c07d965b9447d2e8b16` | Current hardened paper-validation/runtime spine |
| Beta-spy `main` | `6fc415edc99bb04084a199d336ea5711b568fa35` | Current constituent/tape sensor and legacy paper-signal workstation |
| Alpha `research/v2-full-architecture` | research PR #24; this benchmark is written after the Sep-2 hierarchy repair commits | 16-step orchestrator / lifecycle / opportunity / management / learning / governance candidate |
| Beta `research/v2-full-architecture` | research PR #9; Sep-2 tape exporter update `cf20f35b1781ecc9c9f319a2f1e696790d88d879` | Independent V2 constituent/HGB/predictive-state witness |

The research branches are not deployment authority.

## Current main capabilities

Alpha main already supplies most of the hardened operational substrate: production Tradier market data, frozen one-minute state, hierarchical regime labels, multi-horizon forecasts, P/Q distributions, option-chain economics, bounded-risk strategy evaluation, sandbox execution, broker reconciliation, professional position management, replay, validation, and fail-closed `NO_TRADE` behavior.

Beta main independently observes constituent bars, breadth, flow and sector participation, runs causal 5/15/30-minute forecasts, and records/replays its own decision evidence. Beta does not submit broker orders.

## 16-step benchmark

| Step | Required question | Current best implementation | Benchmark status |
|---:|---|---|---|
| 1 | Are we in a definable regime? | Alpha hierarchy + V2 compact regime/support gate | REPAIRING — V1 hierarchy defect found |
| 2 | What regime are we in? | Alpha hierarchy / V2 compact Alpha regime | REPAIRING — horizon separation needed |
| 3 | How long will it last? | V2 blocked risk-set lifecycle survival | PROMISING CHALLENGER |
| 4 | If it ends, where next? | V2 successor distribution | ADVISORY ONLY |
| 5 | Can we make money? | V2 playbook-specific Opportunity/Action-Value logic | RESEARCH SUPPORTED |
| 6 | How do we monetize it? | V2 playbooks + Alpha payoff optimizer | RESEARCH SUPPORTED |
| 7 | When do we execute? | V2 pending-entry state + first-setup/session controls | RESEARCH SUPPORTED |
| 8 | Which options? | Alpha P/Q + full bounded-risk V2 geometry search | STRONG RESEARCH IMPLEMENTATION |
| 9 | Is cost tolerable? | Alpha executable quote/slippage/fee/risk stress + broker preview | STRONG |
| 10 | Execute | Alpha sandbox execution/reconciliation | HARDENED PAPER ONLY |
| 11 | Monitor | Alpha settlement + V2 thesis state | STRONG PAPER IMPLEMENTATION |
| 12 | Exit plan | V2 playbook-specific thesis/target/invalidation/time rules | RESEARCH SUPPORTED |
| 13 | Profit / scale / bail / loss? | V2 HOLD/ADD/SCALE/TAKE_PROFIT/BAIL/SELL_FOR_LOSS/ADJUST/RESTRUCTURE | RESEARCH SUPPORTED |
| 14 | Did it work? | Alpha confirmation/replay + V2 component attribution | STRONG FOUNDATION |
| 15 | What did we learn? | V2 component-specific learning | IMPLEMENTED, NEEDS FORWARD EVIDENCE |
| 16 | Will we do it again? | V2 playbook governance | IMPLEMENTED, NEEDS SAMPLE DEPTH |

## Benchmark failures that must govern the build

### 1. Production Alpha hierarchy is not genuinely hierarchical enough

The replay audit in research PR #25 showed that the existing `conflict_score` is identically zero because its per-level signs are derived from breadth and risk tone that are identical across all four lookbacks. Eight of ten `RegimeState` fields were also identical across horizons. The conflict gate therefore could not detect the exact fast-vs-slow disagreement it was intended to measure.

The Sep-2 V2 research repair changes only the V2 research path:

- breadth and concentration become causal horizon-specific exponentially weighted states;
- volatility and correlation use horizon-specific causal level estimates before classification;
- tied empirical percentiles retain mid-rank treatment;
- hierarchy conflict is weighted disagreement across volatility, correlation, breadth and concentration;
- the existing `>= 0.65` transition threshold remains the downstream contract;
- regression tests require zero conflict for identical levels and actionable conflict for a deliberate fast/slow break.

This repair must pass replay and forward evidence before replacing Alpha main behavior.

### 2. Volatility-floor ties corrupted regime labels

Historical V2 replay found that the old `mean(history <= current)` percentile treatment could map repeated volatility-floor values to the 100th percentile. In the raw 1,426-anchor replay, 1,321 anchors became `EXPANSION`. Tie-aware empirical mid-ranks corrected this pathological classification.

### 3. Completed-same-regime lifecycle was too sparse

The first lifecycle estimator produced only 41 definable timestamps across the 23-session research replay, approximately 87-minute duration MAE, and no usable closed-loop trades. It is rejected as the Step-3 authority design.

The blocked walk-forward discrete-time risk-set replacement produced, on the burned architecture-research sample:

| Metric | Historical research result |
|---|---:|
| Definable anchors | 1,116 |
| 5m survival Brier | ~0.031 |
| 15m survival Brier | ~0.040 |
| 30m survival Brier | ~0.058 |
| Duration MAE for observed <=35m transitions | ~14.1 min |
| Successor-regime accuracy | ~50.6% |

Interpretation: Step 3 is a credible forward-validation challenger. Step 4 is not.

### 4. Successor direction is not allowed to veto trades yet

Approximately 50.6% historical successor accuracy is not enough to make Step 4 hard authority. The V2 survival engine therefore keeps successor direction advisory until at least 100 scored lifecycle forecasts and >=60% scored transition accuracy. Those thresholds are research governance, not a claim that 60% alone proves production edge.

### 5. A signal is not a fresh trade opportunity

The repaired lifecycle combined with a monolithic every-signal-to-trade rule overtraded: 12 trades, approximately -$188.60 at the synthetic 20% IV research baseline, with ~16.7% winners. That architecture is rejected.

Step 5 must be playbook-specific. The directional control lane uses the first qualifying setup/session rather than re-arming on every later HGB print. P/Q may improve expression geometry but does not retroactively revoke a separately validated directional witness merely because a synthetic valuation model dislikes it.

### 6. Management must be playbook-specific

The architecture-research replay found that generic aggressive management hurt the directional lane while profit protection helped the late-range lane. There is no universal T+15 exit. Steps 11-13 must manage the frozen thesis/playbook, not a generic P&L threshold.

## Burned historical research benchmark

The Jul-27 through Aug-26 architecture sample is burned for design research. Complete historical option chains were unavailable for much of it, so option P&L is synthetic and cannot be promoted as actual execution evidence.

Under corrected, playbook-specific research logic:

| Lane | Trades | Net research P&L | Win rate | PF |
|---|---:|---:|---:|---:|
| Directional momentum | 18 | +$125.90 | 72.2% | ~4.82 |
| Late range/carry | 10 | +$178.27 | 90.0% | n/a |
| Combined | 28 | +$304.17 | 78.6% | ~10.17 |
| Combined, punitive execution haircut | 28 | +$38.67 | 64.3% | ~1.46 |

The stressed day-level result was not statistically significant. This is evidence that execution quality is first-order, especially for four-leg range structures, not evidence of a production-ready edge.

## Beta authority boundary

Beta V2 remains an independent witness, not a hidden second strategy engine. It may contribute:

- HGB direction and strength;
- constituent breadth/flow state;
- meaningful-move and path probabilities;
- a non-authoritative independent regime/lifecycle view for disagreement diagnostics.

It may not define Alpha's authoritative regime, select the final payoff geometry, bypass Alpha lifecycle support, or submit an order.

The Sep-2 Beta research tape exporter preserves Beta factors, forecasts and explicit decisions together with the full Alpha replay evidence so future cross-model tests do not have to infer the Beta witness from downstream actions.

## CI benchmark

At the benchmark start:

- Alpha main's latest CI had passing Python 3.11/3.12 tests, static validation and workstation build, but the workflow was red on Ruff.
- Alpha PR #24 likewise had passing Python 3.11/3.12 tests/static/frontend on its last completed run and was red on Ruff.
- Beta main and Beta PR #9 were red in the test step; install/compile/dashboard-asset checks passed. A separate Beta research PR reports a pre-existing legacy session-bias test failure on main, but the exact failure must still be treated as CI debt until independently confirmed/fixed.

A red repository is not promotion-ready even when the red check appears unrelated to trading logic.

## Forward validation gates

The next clean evidence source is forward captured actual-chain data. Before any V2 paper authority is considered, the system must demonstrate all of the following as separate questions:

1. **Regime hierarchy:** replay of stable and transition tapes must show low false conflict in stable conditions, real cross-horizon conflict at deliberate state breaks, and materially less label churn than the rejected hierarchy.
2. **Lifecycle persistence:** forward 5/15/30 survival calibration and duration error must remain useful out of sample; no current-session future labels may enter fitting.
3. **Successor direction:** remains telemetry until its own governance gate is earned; no indirect bypass through Step 5.
4. **Opportunity:** each playbook must show independent setup episodes and action value; repeated timestamps from one setup are not independent trades.
5. **Options/economics:** actual archived chains, executable bid/ask, actual fee previews/fills and deterministic maximum loss must support the selected geometry.
6. **Management:** exits are compared with frozen thesis, MFE/MAE and actual executable liquidation economics; fair value and executable value remain separate.
7. **Learning/governance:** one outcome never promotes, retires or rewrites a component. Unsupported attribution remains `UNKNOWN`.
8. **Safety:** V2 remains paper/research only until forward evidence and the existing production promotion controls independently pass.

## Immediate build direction

The correct next architecture is not a new monolithic trader. Continue the specialized-engine V2 branch, with this authority order:

`Alpha repaired regime -> Alpha risk-set survival -> advisory successor forecast -> Beta independent witness -> playbook-specific action value -> Alpha current-chain payoff/economics -> sandbox execution -> thesis-specific management -> component attribution -> playbook governance`.

The first engineering priority is to falsify the repaired Step-1/2 hierarchy with the replay harness. The second is to accumulate forward full-chain lifecycle and cross-model witness evidence. Step 4 should not be promoted merely to make the decision tree look complete.

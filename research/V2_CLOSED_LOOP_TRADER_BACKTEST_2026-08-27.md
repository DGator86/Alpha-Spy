# V2 Closed-Loop Trader Chronological Replay — 2026-08-27

## Scope

Research/paper only. Historical market state is reconstructed causally from the archived Jul-27 through Aug-26 constituent/SPY tapes. Historical complete option chains were not captured for most of this period, so historical option P&L remains synthetic and must not be represented as actual fills. Forward full-chain capture remains the deployment-grade validation path.

The governing sequence is:

`Alpha regime -> lifecycle survival/transition -> Beta witness -> monetizable edge/playbook -> entry timing -> option expression/economics -> execution -> thesis management -> attribution/governance`.

## Replay data

- 23 sessions, Jul 27-Aug 26, 2026.
- 1,426 five-minute causal decision anchors.
- ~500 constituent minute bars plus SPY.
- Existing causal HGB directional predictions and predictive-state telemetry.
- Existing state/P-Q counterfactual option-action ledger.
- One open position at a time; independent sequential playbooks may trade in the same session.
- <= $100 modeled risk per unit/setup remains the comparison risk boundary.

## Defect 1 — tied realized-vol percentile

The repository regime classifier computes volatility percentile as `mean(history <= current)`. Realized volatility is floored at `0.00045`; repeated observations exactly at the floor therefore rank at the 100th percentile and can be labeled `crisis` instead of quiet/low volatility.

In the raw replay this defect classified 1,321 / 1,426 anchors as `EXPANSION`, which is economically implausible and makes the lifecycle unusable.

Research correction: tie-aware empirical mid-rank:

`q = mean(history < current) + 0.5 * mean(history == current)`.

After correction the reduced-data Alpha regime reconstruction was:

- QUIET: 1,257
- DIRECTIONAL_UP: 74
- DIRECTIONAL_DOWN: 42
- EXPANSION: 36
- TRANSITION: 17

This is a required production-code fix.

## Defect 2 — completed-episode-only lifecycle is too sparse

The first authoritative lifecycle implementation required >=10 completed same-regime episodes and effective episode sample size >=6. On this 23-session replay it produced only 41 definable timestamps, all QUIET, with the first definable state not appearing until Aug 17.

Observed lifecycle quality under that estimator:

- survival Brier: ~0.157
- duration MAE: ~87 minutes
- successor accuracy: 0%

The exact current closed-loop agent therefore produced **zero trades** because Steps 3-4 almost never became authoritative.

### Replacement research estimator

A blocked walk-forward discrete-time survival/risk-set model was tested. It is fit only on prior sessions and predicts survival at 5/15/30 minutes. No current-day future label is used.

Results from Aug 3 onward:

- definable anchors: 1,116
- 5m survival Brier: ~0.031
- 15m survival Brier: ~0.040
- 30m survival Brier: ~0.058
- duration MAE for observed <=35m transitions: ~14.1 minutes
- successor-regime accuracy: ~50.6%

Conclusion: **Step 3 persistence/duration is useful; Step 4 successor direction is not yet sufficiently accurate to receive hard veto authority.** Successor forecasts should remain advisory/challenger telemetry until independently calibrated above chance.

## Defect 3 — every qualifying HGB print is not a new edge

Feeding the repaired lifecycle into the current monolithic trader rules caused overtrading. The agent treated each HGB-eligible timestamp as a fresh directional opportunity and then selected a P/Q structure.

Result:

- 12 trades
- net approximately **-$188.60** at the 20% synthetic-IV baseline
- win rate ~16.7%

This contradicts the actual historical evidence. The directional HGB lane was validated as the **first qualifying directional setup of the session/setup episode**, not every later signal.

Also, `v2_hgb_vertical.py` correctly states that legacy/state P/Q EV is not the execution authority for the validated HGB control lane. P/Q may improve geometry, but it must not revoke the validated directional signal merely because a synthetic EV model dislikes it.

## Corrected Step 5 — playbook-specific opportunity authority

The corrected closed-loop replay treats `Can we make money?` as a playbook-specific question.

### Playbook A — directional momentum

- independently validated causal HGB/breadth witness
- first qualifying setup/session
- state/lifecycle context may size confidence and invalidate the thesis, but immature successor direction does not hard-veto the setup
- existing $2 ATM debit vertical remains incumbent/control geometry unless a challenger robustly improves execution-adjusted action value
- expected monetization horizon ~15m; aggressive generic dynamic exits previously reduced this lane's performance

Historical replay, Aug 3-Aug 26:

- 18 trades
- net **+$125.90** at 20% synthetic IV
- 72.2% winners
- PF ~4.82
- max drawdown ~-$14.19

### Playbook B — late-session range/carry

A separate action-value model is trained expanding-window, day by day, using only prior sessions. The setup is eligible only in the late-session window when Alpha lifecycle persistence supports quiet/range behavior and Beta big-move probability remains low. Current QUIET or a sufficiently likely transition into QUIET may be monetized.

Walk-forward range entries start Aug 13 because five prior sessions are required for action-value training.

10 strictly day-walk-forward trades:

- Aug 13: +$20.06
- Aug 14: +$21.48
- Aug 17: +$24.82
- Aug 18: +$17.16
- Aug 19: -$0.18
- Aug 20: +$16.07
- Aug 21: +$21.47
- Aug 24: +$17.96
- Aug 25: +$19.82
- Aug 26: +$19.61

Total **+$178.27**, 9/10 winners.

The Aug 19 candidate would have been approximately -$13.91 at its +30m horizon; thesis/profit-protection management closed it near flat after prior favorable excursion. This is direct evidence that Steps 11-13 can add value for the range playbook.

## Combined closed-loop chronological replay

Directional playbook + strictly walk-forward late-range playbook, one open position at a time:

- sessions represented: 18 (Aug 3-Aug 26)
- trades: **28**
- net: **+$304.17**
- average/trade: **+$10.86**
- win rate: **78.6%**
- PF: **~10.17**
- max drawdown: **~-$12.93**
- positive trading days: **15 / 18**

Daily P&L:

| Date | P&L |
|---|---:|
| Aug 03 | -$4.14 |
| Aug 04 | +$9.35 |
| Aug 05 | -$1.90 |
| Aug 06 | +$25.84 |
| Aug 07 | +$6.91 |
| Aug 10 | +$10.56 |
| Aug 11 | +$4.37 |
| Aug 12 | +$16.01 |
| Aug 13 | +$25.37 |
| Aug 14 | +$27.55 |
| Aug 17 | +$25.44 |
| Aug 18 | +$39.68 |
| Aug 19 | -$12.93 |
| Aug 20 | +$27.70 |
| Aug 21 | +$45.27 |
| Aug 24 | +$33.86 |
| Aug 25 | +$15.26 |
| Aug 26 | +$9.99 |

The descriptive exact day sign-flip probability for this selected/burned historical sample is ~0.00019. **This must not be presented as clean confirmatory significance** because architecture/playbook research has already inspected this period.

## Execution torture test

A deliberately punitive stress haircut was applied from the previously measured family-specific execution stress:

- two-leg directional vertical: subtract $5.48/trade versus baseline
- one-leg long call challenger: subtract $2.74
- four-leg iron butterfly: subtract $16.96/trade

Combined stressed result:

- net **+$38.67**
- average/trade: **+$1.38**
- trade win rate: **64.3%**
- PF: **~1.46**
- max drawdown: **~-$35.37**
- positive days: **12 / 18**
- stressed day-level sign-flip is not significant (~0.263)

Interpretation: the baseline research edge is substantial, but four-leg range execution quality is a first-order risk. Real actual-chain forward replay is required before promoting the range lane.

## Required architecture changes before forward authority

1. Fix Alpha volatility percentile ties with a mid-rank/ECDF treatment so the volatility floor cannot map to `crisis`.
2. Replace the sparse completed-episode-only Step-3 estimator with a blocked walk-forward discrete-time survival / hierarchical risk-set lifecycle model.
3. Keep Step-4 successor direction advisory until transition calibration materially exceeds chance and remains stable OOS.
4. Make Step 5 an explicit **Opportunity / Action-Value Engine** with playbook-specific eligibility. A signal is evidence; it is not automatically a new trade.
5. Preserve the validated first-qualifying directional setup rule. Do not allow every later HGB print to re-arm the directional playbook.
6. Do not require legacy/state P/Q EV to authorize the validated HGB control lane. Use P/Q to select/replace geometry only when a challenger earns it.
7. Keep the late-range playbook separate. It must earn authority from walk-forward action value + lifecycle persistence + low big-move probability + actual-chain execution quality.
8. Management rules remain playbook-specific. Dynamic profit protection helped the range lane; generic aggressive management hurt the directional lane.
9. Continue to default to NO_TRADE for unvalidated playbooks.
10. Forward actual-chain data is the next clean evidence source; this historical period is burned for architecture research.

## Bottom line

The backtest rejects the monolithic trader and supports the user's closed-loop architecture **only when each step has separate authority and Step 5 is playbook-specific**.

- exact sparse-lifecycle agent: no usable trading authority
- repaired lifecycle + monolithic signal-to-trade agent: loses heavily from overtrading
- repaired lifecycle + evidence-governed playbooks + thesis-specific management: **+$304.17 baseline over 28 chronological trades**

This is a research result, not a guarantee of future profitability.
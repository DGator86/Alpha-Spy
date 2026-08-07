# Audit-Control-and-Learning Process

## Three independent tapes

1. **Prediction tape:** T=0 market state, features, model version, probability distribution and candidates.
2. **Execution tape:** decision, preview, order events, fills, position marks, exits and reconciliation state.
3. **Confirmation tape:** T+15 realized state, historical revision check, forecast scores and counterfactual strategy outcomes.

The confirmation tape cannot initiate, alter or exit a trade.

## Health controller

The trust score combines data coverage, freshness, model stability and regime evidence. Health gates are:

- Green: normal configured risk
- Yellow: reduced risk and no new entries under the default policy
- Orange: observation/shadow state
- Red: no new entries

Risk may be reduced immediately. It may not be increased intraday because of recent performance.

## Scoring

- Direction accuracy
- Brier score
- Terminal-price error
- Interval coverage
- Path high and low
- Data-revision status
- Candidate T+15 P&L
- Trade MFE/MAE
- Net expectancy after modeled transaction costs

## Failure attribution

Failures are separated into forecast direction, forecast magnitude/range, volatility, structure selection, execution, exit management, risk control and data integrity.

## Champion/challenger governance

A challenger progresses through:

1. Offline time-ordered walk-forward evaluation
2. Historical quote replay
3. Live-data shadow mode
4. Paper mode
5. Restricted one-contract production mode
6. Explicit promotion

Minimum default promotion evidence is 20 independent sessions and 500 non-overlapping forecasts, with improved net expectancy, equal or better calibration and no deterioration in tail risk.

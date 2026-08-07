# Runtime Data Model

## Core tables

| Table | Purpose |
|---|---|
| `market_snapshots` | Immutable minute-level market state |
| `snapshot_quotes` | Point-in-time SPY and constituent quotes |
| `option_chain_snapshots` | Strategy and IV-reference option-chain headers |
| `option_quotes` | Point-in-time option quotes and Greeks |
| `constituent_iv_observations` | Rotating constituent ATM IV and 25-delta skew observations |
| `surface_metrics` | SPY-versus-constituent IV/skew summaries |
| `features` | Frozen constituent, dependence and health features |
| `predictions` | Immutable T+15 forecasts and version hashes |
| `candidates` | All selected and rejected strategy candidates |
| `decisions` | Trade/no-trade decisions and reasons |
| `orders` | Paper and broker order lifecycle records |
| `positions` | Managed position state, MFE/MAE and outcomes |
| `prediction_outcomes` | T+15 forecast results |
| `candidate_outcomes` | Counterfactual T+15 strategy outcomes |
| `data_revision_checks` | Frozen snapshot versus historical API comparison |
| `alerts` | Operational and audit alerts |
| `service_heartbeats` | Service status and latency |
| `control_state` | Operator and processing state |
| `model_versions` | Champion/challenger registry |
| `daily_metrics` | Session summaries |

## Immutable key

`prediction_id` is the primary audit key. Features, candidates, decision and outcome records all retain the original prediction linkage.

## Formal anchors

Every forecast is stored. Forecasts created on the configured 15-minute grid are marked `formal_anchor=1` and used for primary statistical governance to avoid treating highly overlapping one-minute forecasts as independent observations.

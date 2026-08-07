# Data Specification

## Point-in-time constituent table

| Field | Type | Requirement |
|---|---|---|
| effective_at | UTC timestamp | Required |
| ticker | string | Historical ticker at that time |
| permanent_id | string | Stable security identifier |
| weight | float | Float-adjusted point-in-time weight |
| sector | string | Point-in-time GICS or mapped sector |
| shares | float | Optional but recommended |
| divisor_contribution | float | Optional validation field |

## Equity quote/bar table

Required fields:

`timestamp, ticker, bid, ask, bid_size, ask_size, last, volume, exchange, condition`

Derived:

`mid, microprice, quote_age_ms, log_return, dollar_volume, spread_bps`

## Option quote table

Required fields:

`timestamp, underlying, option_symbol, expiration, strike, right, bid, ask, bid_size, ask_size, volume, open_interest`

Required reference data:

- Contract multiplier
- Exercise style
- Settlement type
- Deliverable adjustments
- Last trade date
- Expiration timestamp

## Events and financing

- Risk-free curve by tenor
- SPY and constituent dividend forecasts
- Ex-dividend dates
- Earnings timestamps and confidence
- Borrow/short-fee proxy
- Corporate actions
- Index additions/deletions and rebalance effective times

## Synchronization rules

- All timestamps normalized to UTC, with exchange-local calendar retained.
- Backward as-of joins only.
- Quote-age threshold recorded per observation.
- No forward filling over halts or session boundaries.
- Equity, option, future, and index snapshots must carry an explicit synchronization-quality score.

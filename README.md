# Alpha-SPY v3.0.0 — Paper Validation Candidate

Alpha-SPY is a standalone SPY options decision, execution, audit, replay and governance system. v3.0.0 is intentionally optimized to get as close as practical to a real-money production environment **without risking real money**.

## Authoritative operating mode

- **Decision data:** Tradier **production** real-time REST + websocket market data.
- **Execution:** Tradier **sandbox** virtual account only during proof/promotion.
- **Model snapshots:** synchronized 1-minute frozen observations.
- **Entry evaluation:** 5-minute grid, maximum one new trade per day by default.
- **Position monitoring:** every 60 seconds with strategy-aware exits and a 15:55 ET forced flat.
- **Production money:** locked. Paper-validation success creates only `ELIGIBLE_FOR_MANUAL_LIVE_REVIEW`; it never flips the broker to production.

Production market-data and sandbox execution credentials are physically separated in configuration and client construction. A production market-data token is never accepted as an execution credential.

## Decision architecture

```text
Tradier production websocket + REST
    │
    ├─ SPY + point-in-time SPY constituents
    ├─ QQQ / IWM cash proxies for NQ / RTY context
    ├─ HYG credit proxy
    ├─ UUP dollar proxy
    ├─ SHY / IEF / TLT Treasury-state proxies
    ├─ VIX / VIX9D / VIX3M when available
    ├─ 11 SPDR sector ETFs
    ├─ SPY option chain / IV / Greeks / OI
    ├─ rotating constituent IV / skew observations
    └─ streamed quote/trade microstructure proxies
                 │
                 ▼
        1-minute frozen market state
                 │
                 ├─ deterministic constituent features
                 ├─ hierarchical regime engine
                 │    micro / intraday / swing / structural
                 ├─ physical P distribution
                 │    Student-t + dynamic constituent covariance
                 ├─ synthetic risk-neutral Q distribution
                 │    constituent smiles + implied/realized correlation
                 ├─ correlation-risk-premium estimate
                 ├─ path/touch/reversal/squeeze/MFE/MAE forecasts
                 └─ multi-horizon forecast stack
                      5m / 15m / 30m / 60m / 120m / EOD / 1D / 5D
                                │
                                ▼
                     uncertainty + cost stress
                                │
                                ▼
                    defined-risk option utility
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                 NO_TRADE              hard risk veto
                                            │
                                            ▼
                                  Tradier sandbox paper order
                                            │
                                  preview / fill / partial-fill
                                  broker reconciliation
                                            │
                                            ▼
                                 professional position manager
                                            │
                                  actual-fill P&L + audit tape
                                            │
                                            ▼
                                confirmation / replay / Dojo
                                            │
                                            ▼
                                  paper promotion evaluator
```

### Multi-horizon cadence

The high-frequency decision stack is refreshed every minute: 5m, 15m and 30m. 60m, 120m and EOD refresh every five minutes. 1D and 5D refresh every 15 minutes. This keeps the full state current without refitting the expensive constituent covariance/P-Q stack unnecessarily eight times per minute.

The **15m forecast remains the trade horizon**. 5m/15m/30m alignment participates in strategy qualification; longer horizons are advisory/research context.

## Regime architecture

Regimes are hierarchical rather than one flat label. Each level persists:

- volatility: low / normal / high / crisis;
- correlation: falling / stable / rising / dislocated / unknown;
- breadth: broad-up / mixed / broad-down;
- concentration;
- SPY dealer-gamma **proxy** state;
- opening / midday / final-hour / expiration session;
- event state;
- risk-on/off context;
- VIX term-state when available;
- liquidity state;
- transition/conflict risk across horizons.

No proxy is mislabeled as a direct feed. Tradier does not provide futures, so SPY/QQQ/IWM are explicitly carried as cash-market proxies. SHY/IEF/TLT are explicitly Treasury ETF proxies.

## P/Q and option selection

The physical distribution is built from the timestamped constituent tape using dynamic covariance, weighted constituent means and Student-t innovations. The Q distribution is independently synthesized from constituent IV smiles and dependence, including the observed difference between implied and realized correlation when sufficient IV coverage exists.

Candidates are repriced at the **forecast horizon with remaining 0DTE tenor**. They are not treated as though the option expires at T+15. Candidate eligibility includes:

- executable entry bid/ask;
- horizon liquidation spread/slippage;
- round-trip fees;
- P expected P&L and probability of profit;
- Q-relative value;
- model uncertainty and edge-to-uncertainty;
- **doubled-cost expected value**;
- multi-horizon directional alignment;
- regime/path compatibility;
- deterministic maximum loss and buying-power vetoes.

`NO_TRADE` is a first-class output.

## Position behavior

The live paper position manager uses the same professional logic as research, rather than one global +50%/-40% rule. Depending on structure it applies:

- debit/credit risk stops;
- capped-structure profit targets;
- MFE-based trailing protection;
- directional thesis invalidation;
- volatility-edge invalidation;
- short-strike/boundary threat exits;
- move-complete exits;
- time stops;
- late-session risk reduction;
- hard forecast-horizon exit;
- operator flatten;
- 15:55 ET forced flat.

Held contracts are quoted directly when the candidate chain no longer contains them, so a missing chain row cannot defeat a forced exit. Broker positions are reconciled before entries and during management. Partial fills are adopted as managed exposure rather than ignored.

## Event input

Event risk is a versioned input, not a web scrape inside the decision loop. Configure an authoritative JSON calendar feed with:

```bash
sudo /opt/alpha-spy/release/scripts/configure_event_calendar.sh
```

The local copy contains `generated_at`, `valid_from`, `valid_through`, source metadata and timestamped events. Missing, stale, malformed or out-of-coverage calendars block entries by default. Every accepted refresh is archived under `/var/lib/alpha-spy/audit/event-calendars/` and therefore enters the normal backup trail.

See `config/events.example.json` for the schema.

## Paper-to-live proof gates

Run manually:

```bash
alpha-spy --config /etc/alpha-spy/config.yaml replay-verify
alpha-spy --config /etc/alpha-spy/config.yaml validate-promotion
alpha-spy --config /etc/alpha-spy/config.yaml promotion-report
```

The nightly validation timer runs the same process automatically. The shipped proof policy is intentionally demanding: at least **60 paper sessions**, **5,000 matured forecasts**, **750 non-overlapping formal-anchor samples at both 15m and 30m**, and **60 closed sandbox trades** before review is even possible. A candidate does **not** pass until every configured gate is satisfied, including:

- at least 99% verified production-stream snapshots and required-input coverage;
- at least 90% full P/Q-ready forecasts;
- at least 90% of 15m and 30m formal-anchor evidence produced by the walk-forward trained Ridge signal rather than the cold-start heuristic;
- 15m direction accuracy >= 53% with a 95% Wilson lower bound >= 50%;
- 30m direction accuracy >= 52.5% with a 95% Wilson lower bound >= 50%;
- Brier-score ceilings plus positive/nonnegative Brier skill versus sample climatology;
- calibrated 15m/30m interval coverage between 72% and 88%, rejecting both under- and over-wide intervals;
- sandbox-only broker execution, >=95% fill rate and mean fill slippage <= $12;
- nonnegative total P&L, profit factor >=1.15 and a nonnegative one-sided 95% expectancy lower bound;
- the most recent 20 trades still nonnegative with profit factor >=1.0;
- maximum drawdown <= $600 and nonnegative realized P&L after a second full modeled friction charge;
- zero unresolved reconciliation failures and no realized loss above 1.10x modeled max loss;
- at least three sufficiently sampled regime buckets covering >=75% of trades, with no negative expectancy in tested regimes;
- deterministic captured-tape replay of the current decision fingerprint with zero mismatches, including the complete frozen P/Q quantile shapes.

The minute-by-minute forecast stream remains available for monitoring and replay, but primary statistical evidence uses non-overlapping horizon-specific anchors so serially overlapping predictions cannot inflate the promotion sample count.

At formal 15m/30m anchors Alpha-SPY also runs a walk-forward **HistGradientBoosting shadow challenger** once it has enough evidence. Its forecast is persisted and scored, but `trading_authority=false`, `affects_risk=false`, and `affects_promotion=false`: the challenger can earn a later manual champion/challenger review but can never silently alter paper or live behavior.

The output can only be `PAPER_VALIDATION_INCOMPLETE` or `ELIGIBLE_FOR_MANUAL_LIVE_REVIEW`. It never enables live money.

### Captured-tape replay

Tradier's normal historical endpoints are not treated as a substitute for a full historical option book. Alpha-SPY therefore builds its authoritative replay corpus **forward from the real-time production feed**. Replay re-computes forecasts from rows timestamped at or before the original observation, uses frozen event/calendar metadata, and reports any deterministic mismatch. Synthetic universes and the Dojo remain useful for pre-tape stress testing; promotion depends on captured real-market tape.

## Install

Build/verify first:

```bash
make venv
make verify
make release
```

Install the release on Ubuntu 24.04:

```bash
sudo bash scripts/install_vps.sh
```

Then configure the two Tradier roles:

```bash
sudo /opt/alpha-spy/release/scripts/configure_tradier.sh
```

Then configure the authoritative event calendar:

```bash
sudo /opt/alpha-spy/release/scripts/configure_event_calendar.sh
```

The Tradier setup script asks for a production **market-data** token and sandbox **paper-execution** token/account separately. Secrets are stored only in `/etc/alpha-spy/secrets.env`.

## Production locks

Even after paper validation passes, real-money execution requires a separate workflow:

1. broker submission disabled;
2. latest `promotion-latest.json` must have passed every gate;
3. `scripts/create_production_approval.py` verifies and hashes that exact report;
4. the approval must match the current model version and entire behavior/validation fingerprint;
5. a separate production execution credential must exist;
6. explicit production unlock phrase and sentinel are still required.

Changing prediction, context, strategy, risk, execution behavior **or promotion thresholds** invalidates the approval fingerprint. `production_lock.sh` removes both the production sentinel and approval artifact.

For the current project goal, leave production execution locked and use the sandbox virtual account as the only broker destination.

## Important limits

- Tradier is the current data/broker interface. Direct ES/NQ/RTY futures, exchange depth/order-book cancellation feeds, direct Treasury yields and institutional complex-order classification are not fabricated when unavailable; the runtime labels proxies and uncertainty explicitly.
- Event-calendar quality is only as authoritative as the configured external source.
- Paper fills are useful evidence but are not identical to real-money market impact. The promotion process compensates partially with executable bid/ask modeling, realized fill statistics and doubled-cost stress; it cannot eliminate that structural difference.

These limitations are reasons for the promotion gates—not reasons to bypass them.

# Alpha-SPY v3.0 Runtime Architecture

## Operating principles

1. Production market data and broker execution are separate trust domains.
2. During validation, **only Tradier sandbox** may receive orders.
3. Prediction, decision, execution, confirmation, replay and promotion are separate immutable tapes.
4. All model inputs are point-in-time; backward as-of joins are mandatory.
5. `NO_TRADE` is the fail-closed response to incomplete data, broker ambiguity, excessive uncertainty or weak economics.
6. Intraday controls may reduce risk, pause entries or flatten; model promotion occurs outside the session.
7. Passing paper validation creates review eligibility only. It cannot enable production automatically.

## Services

### Market service

Uses the production Tradier market-data credential. A production WebSocket maintains current quote/trade context while a synchronized one-minute snapshot is persisted for deterministic modeling. REST collection supplements the stream with constituent snapshots, SPY option chains and rotating constituent IV context.

Context symbols include SPY/QQQ/IWM, sector ETFs, HYG, UUP, SHY/IEF/TLT and optional VIX-family symbols. Direct futures, order-book depth and direct Treasury yields are not fabricated; configured cash/ETF proxies are labeled as proxies.

### Engine service

For each new synchronized snapshot:

1. computes deterministic constituent/features state;
2. constructs context and validates required inputs;
3. resolves event state;
4. classifies micro/intraday/swing/structural regimes;
5. builds active multi-horizon P and Q distributions;
6. calculates path/touch/reversal/MFE/MAE/IV outputs;
7. persists immutable forecasts;
8. on an eligible five-minute grid, generates 15m defined-risk candidates;
9. applies P/Q economics, uncertainty, doubled-cost and strategy-fit filters;
10. reconciles broker state and applies account/risk hard vetoes;
11. records `NO_TRADE` or a paper-order decision;
12. submits only through the sandbox execution client when configured.

### Settlement service

Runs every minute. It reconciles local and sandbox broker exposure, directly quotes held contracts when needed, computes executable liquidation marks and applies professional family-specific exit policy. Local closure occurs only after broker-authoritative execution when broker submission is enabled. Actual fills and fees are used for realized P&L.

### Confirmation service

Matures each forecast horizon independently. It attaches point-in-time realized outcomes, path extrema and calibration fields without modifying the original forecast.

### Replay verifier

Samples matured forecasts across the captured tape and recomputes them from state available at their creation time. Feature/config hashes and P/Q/regime/numeric outputs must match. Missing state or drift is recorded as a mismatch.

### Validation service

Runs after-session promotion analysis. It combines forecast calibration, paper trades, fill quality, drawdown, doubled-cost stress, regime stability, broker reconciliation and deterministic replay. It writes a signed-by-content evidence artifact for operator review.

### Dojo

Runs post-session research/governance analysis. It does not mutate the live model intraday.

### Dashboard / decision API

Bind to loopback by default. The dashboard exposes the authoritative 15m trade forecast plus the full horizon stack, regime hierarchy, context/input health, P/Q diagnostics, current professional position-management state, replay status and promotion gates. Authenticated operator commands are queued; the execution engine remains authoritative.

## Persistence

SQLite WAL is the authoritative journal for structured runtime state. Raw high-volume market observations are additionally written to dated JSONL records. Validation/replay/event-calendar evidence is retained under the state root and included in backups.

Key persisted domains include:

- synchronized market snapshots and option chains;
- constituent IV observations;
- deterministic feature states;
- immutable multi-horizon predictions;
- candidates and decisions;
- broker orders, positions and outcomes;
- confirmation/revision checks;
- model versions and controls;
- validation runs and replay runs.

## Credential boundary

Secrets are loaded from `/etc/alpha-spy/secrets.env` and are never committed.

- Market token: production market data only; no order account is attached to this client.
- Paper execution token/account: sandbox only.
- Production execution credentials are a separate optional path and remain locked during paper validation.

`paper_mode=true` means broker orders may go to the sandbox virtual account. `submit_orders=false` means no broker order is submitted at all.

## Production boundary

Real-money submission requires all of the following independently:

- production execution environment and credentials;
- trading/submission explicitly enabled with paper mode off;
- maximum-contract safety constraints;
- production sentinel;
- current promotion evidence with successful manual-review eligibility;
- an approval artifact whose evidence SHA, model version and configuration/validation fingerprint match the running system.

Changing model logic, risk controls or promotion thresholds invalidates the previous approval. No validation command writes the production sentinel or turns live submission on.

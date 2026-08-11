# Tradier Setup

## Alpha-SPY paper deployment

Alpha-SPY uses two isolated Tradier connections:

1. **Production market data** — production API token, real-time equities/options REST data and the production websocket stream. This client has no configured order account.
2. **Sandbox execution** — sandbox API token plus virtual account ID. This client is used only for previews, paper orders, order status, positions, balances and reconciliation.

The sandbox market-data feed is not used for model decisions.

## Configure credentials

```bash
sudo /opt/alpha-spy/release/scripts/configure_tradier.sh
```

The script prompts separately for the production market-data token, sandbox execution token, and sandbox virtual account. It writes them to `/etc/alpha-spy/secrets.env`, enables the production websocket, enables broker submission to the sandbox, and keeps `paper_mode=true`.

Never commit `/etc/alpha-spy/secrets.env` or paste real tokens into `config/suite.yaml`.

## Expected runtime posture

```text
market data environment : PRODUCTION
market transport        : websocket + REST
execution environment   : SANDBOX
paper_mode              : true
submit_orders            : true
maximum contracts       : 1
```

`submit_orders=true` means Alpha-SPY sends the selected order to the Tradier virtual account. It does **not** mean live-money trading while the execution environment is sandbox and `paper_mode=true`.

## Sandbox validation checklist

- Production stream remains connected and current.
- SPY/constituent model snapshots are persisted once per minute.
- SPY option chains and IV context come from the production REST API.
- Dashboard shows market data as production and execution as paper/sandbox.
- Sandbox balances, positions, current-session orders and fills reconcile with the local journal.
- No unconfirmed or partially filled managed order is left unmanaged.
- Cancel/replace and terminal-order handling are verified.
- T+15 confirmation outcomes mature.
- Forecast-horizon exits and 15:55 forced-flat behavior are verified.
- Restart recovery/reconciliation is verified.
- Google Drive backup completes.

Tradier sandbox historical account history is not required by Alpha-SPY; runtime reconciliation uses current balances, positions and current-session orders.

## Production

Live-money execution is a separate mode and remains locked by the production sentinel plus the evidence-bound production approval artifact. The production market-data credential by itself can never authorize an order.

```bash
sudo /opt/alpha-spy/release/scripts/production_unlock.sh
```

Lock broker submission immediately with:

```bash
sudo /opt/alpha-spy/release/scripts/production_lock.sh
```

A dedicated live execution account is strongly preferred. The current project target is sandbox paper execution until the promotion gates are deliberately completed.

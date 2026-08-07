# Tradier Setup

## Configure credentials

```bash
sudo /opt/alpha-spy/release/scripts/configure_tradier.sh
```

Supply the sandbox token and sandbox account ID first. The script stores credentials in `/etc/alpha-spy/secrets.env` with restricted permissions and restarts the suite.

## Sandbox validation checklist

- Quotes and option chains update every minute.
- Dashboard environment reads `SANDBOX`.
- Market and engine services remain `ONLINE`.
- Paper decisions and position marks appear correctly.
- No unconfirmed order remains open.
- Cancel and terminal-order handling are verified.
- T+15 confirmation outcomes mature.
- Forced-flat behavior is verified.
- Google Drive backup completes.

## Production

Production is intentionally a two-key operation: configuration plus `/etc/alpha-spy/PRODUCTION_UNLOCKED`.

```bash
sudo /opt/alpha-spy/release/scripts/production_unlock.sh
```

Lock it again immediately with:

```bash
sudo /opt/alpha-spy/release/scripts/production_lock.sh
```

A dedicated account is strongly preferred. The system's daily-loss ledger tracks positions managed by this suite; unrelated manual or third-party positions are not part of that internal ledger.

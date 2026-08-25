# VPS parity

The trading box is **srv1874660 / `82.29.155.71`**. Full Alpha stack (market, engine, decision, settlement, confirmation, dashboard) plus Beta. Overlay and restart only here.

The clone **srv1575978 / `2.24.28.77`** was destroyed. Do not SSH there. `ssh vps` in this environment must resolve to `82.29.155.71`.

Deploy Alpha with a surgical overlay into `/opt/alpha-spy/release` and the venv (`pip install --no-deps .`), then restart the service that loaded the module. Do not `git pull` / `git reset` the dirty release tree. Do not run `deploy_vps.sh`.

This box runs Alpha-SPY and Beta-spy only as application stacks. Docker / Iron-Spyder / containerd are masked. Tailscale stays up for remote access. Beta live unit uses `--warm-sessions 0` so the Tradier tape attaches immediately after restart (warmup would block the websocket for many minutes during RTH).

Live production fixes that Boston was missing (engine dead since 2026-08-12):

- pandas 3 copy-on-write crash in `distributions.py` (`corr.values` is read-only; use `to_numpy(copy=True)`)
- `INSERT OR REPLACE` on engine journal rows (`features`, `predictions`, `candidates`, `decisions`) so a retried cycle does not UNIQUE-fail after a mid-cycle crash
- Tradier empty `orders`/`positions`/`quotes` payloads are the string `"null"`; treat non-objects as empty so broker reconciliation cannot take down the engine
- off-hours 500-name tape capture skip in `services.py` / `streaming_market.py`
- rolling ordinary-session calendar (`scripts/ordinary_calendar.py`)
- capture retention (`scripts/prune_capture.sh`)

Beta-SPY cannot be pushed to `DGator86/Beta-spy` from this agent (GitHub App
has no write access). Push Beta from the VPS as DGator86. Boston Beta lives at
`/opt/beta-spy` with DB `/var/lib/beta-spy/beta-spy.sqlite`.

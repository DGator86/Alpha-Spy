# VPS parity

There are two Hostinger boxes. They are not interchangeable.

| Host | Address | Role |
| --- | --- | --- |
| **srv1874660** | **`82.29.155.71`** | **Boston trading VPS.** Full Alpha stack (market, engine, decision, settlement, confirmation, dashboard) plus Beta. This is the machine to overlay and restart. |
| srv1575978 | IPv6 `2a02:4780:75:cbfb::1` (`ssh vps` in some agent environments) | Separate clone. Do not treat `ssh vps` as Boston. |

`ssh vps` in the cloud-agent environment currently lands on **srv1575978**, not 82.29.155.71. Always target `root@82.29.155.71` with `~/.ssh/vps_key` for trading work.

Deploy Alpha with a surgical overlay into `/opt/alpha-spy/release` and the venv (`pip install --no-deps .`), then restart the service that loaded the module. Do not `git pull` / `git reset` the dirty release tree. Do not run `deploy_vps.sh`.

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

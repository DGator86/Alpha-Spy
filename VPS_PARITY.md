# VPS parity

This branch is the Alpha-SPY source that is running on the Boston VPS
(`82.29.155.71`). Deploy these files into the venv (`pip install .` from
the release tree, then restart `alpha-spy-engine` and `alpha-spy-market`).

Live production fixes on this branch:

- pandas 3 copy-on-write crash in `distributions.py`
- off-hours 500-name tape capture skip in `services.py` / `streaming_market.py`
- rolling ordinary-session calendar (`scripts/ordinary_calendar.py`)
- capture retention (`scripts/prune_capture.sh`)

Beta-SPY cannot be pushed to `DGator86/Beta-spy` from this agent (GitHub App
has no write access). The complete Beta tree that matches the VPS lives on
the sibling branch `cursor/beta-spy-vps-parity-d73a` in this same repo until
that access is granted. The VPS also keeps a bare mirror at
`/root/beta-spy.git`.

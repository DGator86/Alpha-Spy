from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .db import Journal
from .v2_hardening import V2HardenedEngineService


def main() -> None:
    parser = argparse.ArgumentParser(prog="alpha-spy-v2")
    parser.add_argument("--config", default="/etc/alpha-spy/config.yaml")
    parser.add_argument("--state-root", default=None)
    parser.add_argument("--beta-state-url", default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.state_root:
        root = Path(args.state_root)
        config.paths.state_root = root
        config.paths.database = root / "journal" / "alpha-spy.db"
        config.paths.dashboard_database = root / "dashboard" / "command-center.sqlite"
        config.paths.universe_cache = root / "reference" / "universe.csv"
        config.paths.model_dir = root / "models"
        config.paths.report_dir = root / "reports"
        config.create_directories()

    # V2 uses actual executable quotes as the broad-search friction model and
    # broker preview fees for finalists.  Legacy fixed commissions must not be
    # reintroduced into the candidate tournament.
    config.prediction.model_version = "alpha-beta-v2.0.0"
    config.trading.fee_per_contract = 0.0
    config.trading.minimum_slippage = 0.0
    config.trading.slippage_fraction_of_spread = min(
        config.trading.slippage_fraction_of_spread,
        0.10,
    )
    config.strategy.max_width = max(config.strategy.max_width, 10.0)
    config.risk.maximum_trade_risk_dollars = min(
        config.risk.maximum_trade_risk_dollars,
        100.0,
    )
    config.risk.maximum_contracts = 1

    journal = Journal(config.paths.database)
    service = V2HardenedEngineService(
        config,
        journal,
        beta_state_url=args.beta_state_url,
    )
    if args.once:
        result = service.run_once()
        print(result or "idle")
        return
    service.run_forever()


if __name__ == "__main__":
    main()

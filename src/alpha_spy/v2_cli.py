from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .db import Journal
from .v2_services import V2EngineService
from .v2_streaming_market import V2StreamingMarketService


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="alpha-spy-v2",
        description="Alpha-SPY V2 paper/research runtime",
    )
    root.add_argument("--config", default="/etc/alpha-spy/config.yaml")
    root.add_argument("--state-root", default=None)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("market")
    engine = sub.add_parser("engine")
    engine.add_argument("--beta-state-url", default=None)
    once = sub.add_parser("run-once")
    once.add_argument("service", choices=("market", "engine"))
    once.add_argument("--beta-state-url", default=None)
    return root


def _config(args: argparse.Namespace):
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
    # V2 is intentionally paper/sandbox only until blind validation passes.
    config.trading.paper_mode = True
    if config.tradier.environment == "production":
        config.trading.submit_orders = False
    return config


def main() -> None:
    args = parser().parse_args()
    config = _config(args)
    journal = Journal(config.paths.database)

    if args.command == "market":
        V2StreamingMarketService(config, journal).run_forever()
        return
    if args.command == "engine":
        V2EngineService(
            config,
            journal,
            beta_state_url=args.beta_state_url,
        ).run_forever()
        return
    if args.command == "run-once":
        if args.service == "market":
            print(V2StreamingMarketService(config, journal).run_once())
        else:
            print(
                V2EngineService(
                    config,
                    journal,
                    beta_state_url=args.beta_state_url,
                ).run_once()
            )


if __name__ == "__main__":
    main()

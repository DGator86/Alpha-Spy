from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

import uvicorn
import yaml

from . import __version__
from .config import SuiteConfig, load_config
from .db import Journal
from .services import ConfirmationService, DojoService, EngineService, MarketService, SettlementService
from .state import build_dashboard_state
from .universe import UniverseProvider


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alpha-spy", description="Alpha-SPY")
    p.add_argument("--config", default="/etc/alpha-spy/config.yaml")
    p.add_argument("--state-root", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("market", "engine", "confirmation", "settlement"):
        sub.add_parser(name)

    api = sub.add_parser("decision-service")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8787)

    dash = sub.add_parser("dashboard-api")
    dash.add_argument("--host", default=None)
    dash.add_argument("--port", type=int, default=None)

    dojo = sub.add_parser("dojo")
    dojo.add_argument("--recent-days", type=int, default=20)
    dojo.add_argument("--days", type=int, default=None)
    dojo.add_argument("--reports-dir", default=None)
    dojo.add_argument("--configs-dir", default=None)
    dojo.add_argument("--universes", type=int, default=8)
    dojo.add_argument("--generations", type=int, default=2)
    dojo.add_argument("--trials", type=int, default=15)

    sub.add_parser("doctor")
    sub.add_parser("state")
    refresh = sub.add_parser("universe-refresh")
    refresh.add_argument("--force", action="store_true")
    once = sub.add_parser("run-once")
    once.add_argument("service", choices=["market", "engine", "confirmation", "settlement", "dojo"])
    init = sub.add_parser("init-config")
    init.add_argument("path", nargs="?", default="./config.yaml")
    init.add_argument("--force", action="store_true")
    return p


def _config(args: argparse.Namespace) -> SuiteConfig:
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
    return config


def _configure_dashboard_env(config: SuiteConfig) -> None:
    os.environ["DASHBOARD_HOST"] = config.dashboard.host
    os.environ["DASHBOARD_PORT"] = str(config.dashboard.port)
    os.environ["DASHBOARD_DB"] = str(config.paths.dashboard_database)
    os.environ["DASHBOARD_MODE"] = config.dashboard.mode
    os.environ["DASHBOARD_REQUIRE_VIEW_TOKEN"] = str(config.dashboard.require_view_token).lower()
    os.environ["DASHBOARD_VIEW_TOKEN"] = config.dashboard.view_token.get_secret_value()
    os.environ["DASHBOARD_ADMIN_TOKEN"] = config.dashboard.admin_token.get_secret_value()
    os.environ["DASHBOARD_INGEST_TOKEN"] = config.dashboard.ingest_token.get_secret_value()
    os.environ["TRADIER_ENV"] = config.tradier.environment
    os.environ["TRADIER_ACCESS_TOKEN"] = config.tradier.access_token.get_secret_value()
    os.environ["TRADIER_ACCOUNT_ID"] = config.tradier.account_id
    os.environ["TRADIER_READ_ONLY"] = "true"
    os.environ["ENGINE_NAME"] = "Alpha-SPY"
    os.environ["ENGINE_VERSION"] = __version__


def doctor(config: SuiteConfig, journal: Journal) -> int:
    checks = {
        "version": __version__,
        "config": "ok",
        "state_root": str(config.paths.state_root),
        "database": str(config.paths.database),
        "database_quick_check": journal.integrity_check(),
        "dashboard_database": str(config.paths.dashboard_database),
        "tradier_environment": config.tradier.environment,
        "tradier_token_configured": bool(config.tradier.access_token.get_secret_value()),
        "tradier_account_configured": bool(config.tradier.account_id),
        "trading_enabled": config.trading.enabled,
        "submit_orders": config.trading.submit_orders,
        "paper_mode": config.trading.paper_mode,
        "production_unlocked": config.production_is_unlocked(),
        "view_token_configured": bool(config.dashboard.view_token.get_secret_value()),
        "admin_token_configured": bool(config.dashboard.admin_token.get_secret_value()),
        "ingest_token_configured": bool(config.dashboard.ingest_token.get_secret_value()),
    }
    universe_ok = False
    try:
        holdings = UniverseProvider(config).get()
        covered_weight = sum(x.weight for x in holdings)
        checks["universe_count"] = len(holdings)
        checks["universe_weight"] = covered_weight
        universe_ok = (
            len(holdings) >= config.universe.minimum_symbols
            and covered_weight >= config.universe.minimum_covered_weight
        )
    except Exception as exc:
        checks["universe_error"] = str(exc)
    checks["universe_meets_minimum"] = universe_ok

    # Report the conditions that actually gate trading. Reporting "ok" while
    # the universe sits below the configured minimum -- which blocks every
    # entry when block_on_incomplete_universe is set -- tells an operator the
    # opposite of what the risk controller will do.
    warnings: list[str] = []
    if not universe_ok:
        warnings.append(
            "universe below the configured minimum "
            f"({checks.get('universe_count', 0)}/{config.universe.minimum_symbols} symbols, "
            f"{checks.get('universe_weight', 0.0):.3f}/{config.universe.minimum_covered_weight} weight)"
        )
        if config.risk.block_on_incomplete_universe:
            warnings.append("entries are blocked while universe coverage is incomplete")
    checks["warnings"] = warnings

    # Only a broken database is a hard failure: a thin universe is recoverable
    # and fails closed on its own, so it must not abort an installation.
    failed = checks["database_quick_check"] != "ok"
    checks["doctor"] = "failed" if failed else ("degraded" if warnings else "ok")
    print(json.dumps(checks, indent=2, default=str))
    return 1 if failed else 0


def init_config(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"Refusing to overwrite {path}; use --force")
    config = SuiteConfig()
    raw = config.model_dump(mode="json")
    raw["tradier"]["access_token"] = ""
    raw["dashboard"]["view_token"] = secrets.token_urlsafe(32)
    raw["dashboard"]["admin_token"] = secrets.token_urlsafe(32)
    raw["dashboard"]["ingest_token"] = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    print(path)


def main() -> None:
    args = parser().parse_args()
    if args.command == "init-config":
        init_config(Path(args.path), args.force)
        return
    config = _config(args)
    journal = Journal(config.paths.database)

    if args.command == "market":
        MarketService(config, journal).run_forever()
    elif args.command == "engine":
        EngineService(config, journal).run_forever()
    elif args.command == "confirmation":
        ConfirmationService(config, journal).run_forever()
    elif args.command == "settlement":
        SettlementService(config, journal).run_forever()
    elif args.command == "decision-service":
        os.environ["ALPHA_SPY_CONFIG"] = args.config
        uvicorn.run("alpha_spy.decision_api:app", host=args.host, port=args.port, log_level="info")
    elif args.command == "dashboard-api":
        _configure_dashboard_env(config)
        from spy_alpha_dashboard.config import get_settings
        get_settings.cache_clear()
        uvicorn.run(
            "spy_alpha_dashboard.app:app",
            host=args.host or config.dashboard.host,
            port=args.port or config.dashboard.port,
            log_level="info",
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
    elif args.command == "dojo":
        if args.reports_dir:
            config.paths.report_dir = Path(args.reports_dir).parent
        output = DojoService(config, journal).run_once(args.recent_days or args.days or 20)
        print(output)
    elif args.command == "doctor":
        raise SystemExit(doctor(config, journal))
    elif args.command == "state":
        print(json.dumps(build_dashboard_state(config, journal), indent=2, default=str))
    elif args.command == "universe-refresh":
        holdings = UniverseProvider(config).refresh(force=args.force)
        print(json.dumps({"count": len(holdings), "weight": sum(x.weight for x in holdings)}, indent=2))
    elif args.command == "run-once":
        services = {
            "market": lambda: MarketService(config, journal).run_once(),
            "engine": lambda: EngineService(config, journal).run_once(),
            "confirmation": lambda: ConfirmationService(config, journal).run_once(),
            "settlement": lambda: SettlementService(config, journal).run_once(),
            "dojo": lambda: str(DojoService(config, journal).run_once()),
        }
        print(services[args.service]())


if __name__ == "__main__":
    main()

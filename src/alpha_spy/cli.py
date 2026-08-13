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
from .event_calendar import refresh_event_calendar
from .events import event_state_at
from .hardening import HardenedEngineService, HardenedSettlementService
from .replay import ReplayVerifier
from .services import ConfirmationService, DojoService
from .state import build_dashboard_state
from .streaming_market import StreamingMarketService
from .timeutil import utc_now
from .universe import UniverseProvider
from .validation import PromotionEvaluator


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
    event = sub.add_parser("set-event-state")
    event.add_argument(
        "state",
        choices=["ordinary", "earnings_heavy", "macro_announcement", "rebalance", "unknown"],
    )
    event_refresh = sub.add_parser("event-calendar-refresh")
    event_refresh.add_argument("--source", default=None)
    replay = sub.add_parser("replay-verify")
    replay.add_argument("--samples", type=int, default=None)
    validate = sub.add_parser("validate-promotion")
    validate.add_argument("--skip-replay", action="store_true")
    validate.add_argument("--replay-samples", type=int, default=None)
    sub.add_parser("promotion-report")
    refresh = sub.add_parser("universe-refresh")
    refresh.add_argument("--force", action="store_true")
    once = sub.add_parser("run-once")
    once.add_argument("service", choices=["market", "engine", "confirmation", "settlement", "dojo", "validation"])
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
    # Only exported when configured. os.environ wins over the Settings env_file,
    # so unconditionally exporting an empty value would silently override an
    # allow list set in /etc/alpha-spy/dashboard.env.
    if config.dashboard.allowed_origins:
        os.environ["DASHBOARD_ALLOWED_ORIGINS"] = ",".join(config.dashboard.allowed_origins)
    os.environ["TRADIER_ENV"] = config.tradier.environment
    os.environ["TRADIER_ACCESS_TOKEN"] = config.tradier.access_token.get_secret_value()
    os.environ["TRADIER_ACCOUNT_ID"] = config.tradier.account_id
    os.environ["TRADIER_READ_ONLY"] = "true"
    os.environ["ENGINE_NAME"] = "Alpha-SPY"
    os.environ["ENGINE_VERSION"] = __version__


def doctor(config: SuiteConfig, journal: Journal) -> int:
    approval_ok, approval_reason = config.production_approval_status()
    validation = journal.latest_validation_run() or {}
    current_event = event_state_at(config, utc_now())
    checks = {
        "version": __version__,
        "config": "ok",
        "state_root": str(config.paths.state_root),
        "database": str(config.paths.database),
        "database_quick_check": journal.integrity_check(),
        "dashboard_database": str(config.paths.dashboard_database),
        "tradier_environment": config.tradier.environment,
        "tradier_execution_environment": config.tradier.environment,
        "tradier_execution_token_configured": bool(config.tradier.access_token.get_secret_value()),
        "tradier_execution_account_configured": bool(config.tradier.account_id),
        "tradier_market_environment": config.tradier.market_environment,
        "tradier_market_token_configured": bool(config.tradier.market_access_token.get_secret_value()),
        "tradier_market_stream_enabled": config.tradier.stream_enabled,
        "trading_enabled": config.trading.enabled,
        "submit_orders": config.trading.submit_orders,
        "paper_mode": config.trading.paper_mode,
        "execution_mode": (
            "local_paper"
            if not config.trading.submit_orders
            else "tradier_sandbox_paper"
            if config.tradier.environment == "sandbox"
            else "tradier_production_live"
        ),
        "production_unlocked": config.production_is_unlocked(),
        "production_approval": approval_ok,
        "production_approval_reason": approval_reason,
        "production_config_fingerprint": config.production_fingerprint(),
        "paper_validation_status": validation.get("status", "NOT_RUN"),
        "paper_validation_report": str(config.paths.promotion_report),
        "event_calendar_state": current_event.state,
        "event_calendar_source": current_event.source,
        "event_calendar_blocked": current_event.blocked,
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

    warnings: list[str] = []
    if not universe_ok:
        warnings.append(
            "universe below the configured minimum "
            f"({checks.get('universe_count', 0)}/{config.universe.minimum_symbols} symbols, "
            f"{checks.get('universe_weight', 0.0):.3f}/{config.universe.minimum_covered_weight} weight)"
        )
        if config.risk.block_on_incomplete_universe:
            warnings.append("entries are blocked while universe coverage is incomplete")
    if (
        config.trading.enabled
        and config.tradier.stream_enabled
        and not config.tradier.market_access_token.get_secret_value()
    ):
        warnings.append("production market stream is enabled but TRADIER_MARKET_ACCESS_TOKEN is missing")
    if config.tradier.environment == "production" and config.trading.submit_orders and not approval_ok:
        warnings.append(f"production broker submission is locked: {approval_reason}")
    if config.trading.submit_orders and config.tradier.environment == "sandbox" and not config.trading.paper_mode:
        warnings.append("sandbox submission is configured but paper_mode is false")
    if (
        config.trading.enabled
        and config.context.event_calendar_enabled
        and config.context.event_calendar_required
        and current_event.source != "calendar"
    ):
        warnings.append(f"event calendar blocks entries: {current_event.source}")
    if validation and validation.get("status") != "ELIGIBLE_FOR_MANUAL_LIVE_REVIEW":
        warnings.append("paper validation has not passed all promotion gates")
    checks["warnings"] = warnings

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
    raw["tradier"]["market_access_token"] = ""
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
        StreamingMarketService(config, journal).run_forever()
    elif args.command == "engine":
        HardenedEngineService(config, journal).run_forever()
    elif args.command == "confirmation":
        ConfirmationService(config, journal).run_forever()
    elif args.command == "settlement":
        HardenedSettlementService(config, journal).run_forever()
    elif args.command == "decision-service":
        os.environ["ALPHA_SPY_CONFIG"] = args.config
        uvicorn.run("alpha_spy.decision_api:app", host=args.host, port=args.port, log_level="info")
    elif args.command == "dashboard-api":
        _configure_dashboard_env(config)
        from alpha_spy.dashboard.config import get_settings

        get_settings.cache_clear()
        uvicorn.run(
            "alpha_spy.dashboard.app:app",
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
    elif args.command == "set-event-state":
        journal.set_control("event_state", args.state)
        print(json.dumps({"event_state": args.state, "source": "operator_control"}, indent=2))
    elif args.command == "event-calendar-refresh":
        print(json.dumps(refresh_event_calendar(config, args.source), indent=2, default=str))
    elif args.command == "replay-verify":
        result = ReplayVerifier(config, journal).run(args.samples)
        print(json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("status") == "PASSED" else 2)
    elif args.command == "validate-promotion":
        result = PromotionEvaluator(config, journal).run(
            run_replay=not args.skip_replay,
            replay_samples=args.replay_samples,
        )
        print(json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("status") == "ELIGIBLE_FOR_MANUAL_LIVE_REVIEW" else 3)
    elif args.command == "promotion-report":
        if not config.paths.promotion_report.is_file():
            raise SystemExit(f"Promotion report not found: {config.paths.promotion_report}")
        print(config.paths.promotion_report.read_text(encoding="utf-8"), end="")
    elif args.command == "universe-refresh":
        holdings = UniverseProvider(config).refresh(force=args.force)
        print(json.dumps({"count": len(holdings), "weight": sum(x.weight for x in holdings)}, indent=2))
    elif args.command == "run-once":
        services = {
            "market": lambda: StreamingMarketService(config, journal).run_once(),
            "engine": lambda: HardenedEngineService(config, journal).run_once(),
            "confirmation": lambda: ConfirmationService(config, journal).run_once(),
            "settlement": lambda: HardenedSettlementService(config, journal).run_once(),
            "dojo": lambda: str(DojoService(config, journal).run_once()),
            "validation": lambda: json.dumps(PromotionEvaluator(config, journal).run(), default=str),
        }
        print(services[args.service]())


if __name__ == "__main__":
    main()

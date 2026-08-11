from __future__ import annotations

import argparse
from datetime import UTC, datetime

import uvicorn

from .config import get_settings
from .db import Repository
from .demo import base_state, iso, seed_history


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spy-alpha-dashboard")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the dashboard server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--log-level", default="info")
    sub.add_parser("seed-demo", help="Seed demo state and confirmation records")
    sub.add_parser("doctor", help="Validate configuration and database access")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    if args.command == "serve":
        uvicorn.run(
            "alpha_spy.dashboard.app:app",
            host=args.host or settings.dashboard_host,
            port=args.port or settings.dashboard_port,
            log_level=args.log_level,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
        return
    repo = Repository(settings.dashboard_db)
    if args.command == "seed-demo":
        now = datetime.now(UTC)
        seed_history(repo, now)
        repo.set_state("live", base_state(now, 0), iso(now))
        print(f"Demo data written to {settings.dashboard_db}")
        return
    if args.command == "doctor":
        print("version=3.0.0")
        print(f"mode={settings.dashboard_mode}")
        print(f"bind={settings.dashboard_host}:{settings.dashboard_port}")
        print(f"db={settings.dashboard_db}")
        print(f"db_exists={settings.dashboard_db.exists()}")
        print(f"view_token_required={settings.dashboard_require_view_token}")
        print(f"admin_token_configured={bool(settings.dashboard_admin_token)}")
        print(f"ingest_token_configured={bool(settings.dashboard_ingest_token)}")
        print(f"tradier_environment={settings.tradier_env}")
        print(f"tradier_configured={bool(settings.tradier_access_token and settings.tradier_account_id)}")
        print("doctor=ok")


if __name__ == "__main__":
    main()

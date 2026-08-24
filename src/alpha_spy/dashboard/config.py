from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "/etc/alpha-spy/dashboard.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8788
    dashboard_db: Path = Path("/var/lib/alpha-spy/dashboard/command-center.sqlite")
    dashboard_mode: str = Field(default="demo", pattern="^(demo|live)$")
    dashboard_require_view_token: bool = False
    dashboard_view_token: str = ""
    dashboard_admin_token: str = ""
    dashboard_ingest_token: str = ""

    # Browser origins allowed to call the API cross-origin, comma separated.
    # Empty by default: served by FastAPI on the trading host the workstation is
    # same-origin and needs none, and an unset value must never mean "any".
    # Set this only when the UI is hosted elsewhere, e.g.
    #   DASHBOARD_ALLOWED_ORIGINS=https://alpha-spy.vercel.app
    dashboard_allowed_origins: str = ""

    tradier_env: str = Field(default="sandbox", pattern="^(sandbox|production)$")
    tradier_access_token: str = ""
    tradier_account_id: str = ""
    tradier_read_only: bool = True
    tradier_stream_enabled: bool = False

    engine_name: str = "Alpha-SPY"
    engine_version: str = "3.0.0"

    websocket_interval_seconds: float = 1.0
    max_prediction_rows: int = 3000
    max_alert_rows: int = 1000

    @property
    def allowed_origins(self) -> list[str]:
        """Parsed cross-origin allow list.

        A literal "*" is rejected rather than honoured. The dashboard exposes
        account state and accepts operator commands, and wildcard CORS combined
        with a token in browser storage is exactly the configuration that turns
        any page the operator visits into a command channel.
        """
        origins = [item.strip().rstrip("/") for item in self.dashboard_allowed_origins.split(",")]
        return [origin for origin in origins if origin and origin != "*"]

    @property
    def tradier_base_url(self) -> str:
        return (
            "https://sandbox.tradier.com/v1"
            if self.tradier_env == "sandbox"
            else "https://api.tradier.com/v1"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

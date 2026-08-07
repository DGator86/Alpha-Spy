from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "/etc/spy-der/dashboard.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8788
    dashboard_db: Path = Path("/var/lib/spy-der/dashboard/command-center-v2.sqlite")
    dashboard_mode: str = Field(default="demo", pattern="^(demo|live)$")
    dashboard_require_view_token: bool = False
    dashboard_view_token: str = ""
    dashboard_admin_token: str = ""
    dashboard_ingest_token: str = ""

    tradier_env: str = Field(default="sandbox", pattern="^(sandbox|production)$")
    tradier_access_token: str = ""
    tradier_account_id: str = ""
    tradier_read_only: bool = True
    tradier_stream_enabled: bool = False

    engine_name: str = "SPY Constituent Alpha"
    engine_version: str = "2.0.0"

    websocket_interval_seconds: float = 1.0
    max_prediction_rows: int = 3000
    max_alert_rows: int = 1000

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

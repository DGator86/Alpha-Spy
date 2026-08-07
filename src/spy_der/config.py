from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator


class PathsConfig(BaseModel):
    state_root: Path = Path("/var/lib/spy-der")
    database: Path = Path("/var/lib/spy-der/journal/suite-v2.db")
    dashboard_database: Path = Path("/var/lib/spy-der/dashboard/command-center-v2.sqlite")
    universe_cache: Path = Path("/var/lib/spy-der/reference/universe.csv")
    model_dir: Path = Path("/var/lib/spy-der/models")
    report_dir: Path = Path("/var/lib/spy-der/reports")
    log_dir: Path = Path("/var/log/spy-der")
    production_sentinel: Path = Path("/etc/spy-der/PRODUCTION_UNLOCKED")


class TradierConfig(BaseModel):
    environment: Literal["sandbox", "production"] = "sandbox"
    access_token: SecretStr = SecretStr("")
    account_id: str = ""
    timeout_seconds: float = 20.0
    market_data_chunk_size: int = 150
    preview_orders: bool = True
    stream_enabled: bool = False

    @property
    def base_url(self) -> str:
        return (
            "https://sandbox.tradier.com/v1"
            if self.environment == "sandbox"
            else "https://api.tradier.com/v1"
        )

    @property
    def stream_url(self) -> str:
        return "https://stream.tradier.com/v1"


class UniverseConfig(BaseModel):
    source: Literal["ishares_ivv", "local_csv", "fallback"] = "ishares_ivv"
    source_url: str = (
        "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/"
        "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund"
    )
    local_csv: Path = Path("/etc/spy-der/universe.csv")
    refresh_hour_et: int = 7
    maximum_age_hours: int = 36
    minimum_symbols: int = 450
    top_n_quotes: int = 503
    top_n_option_iv: int = 40
    minimum_covered_weight: float = 0.90


class MarketConfig(BaseModel):
    poll_interval_seconds: int = 60
    quote_stale_seconds: int = 120
    option_quote_stale_seconds: int = 180
    collect_only_market_hours: bool = False
    demo_when_unconfigured: bool = True
    include_symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM"])
    option_expiration_mode: Literal["zero_dte", "nearest"] = "zero_dte"
    option_chain_greeks: bool = True
    max_option_chain_rows: int = 800
    constituent_option_batch_size: int = 6
    constituent_iv_refresh_seconds: int = 60
    constituent_iv_max_age_minutes: int = 20
    iv_reference_min_dte: int = 3
    iv_reference_max_dte: int = 14


class PredictionConfig(BaseModel):
    horizon_minutes: int = 15
    minimum_history_rows: int = 30
    return_lookback_rows: int = 60
    distribution_z: float = 1.28
    min_sigma_return: float = 0.00045
    max_sigma_return: float = 0.012
    pressure_coefficient: float = 0.35
    residual_coefficient: float = 0.20
    breadth_coefficient: float = 0.08
    mean_reversion_coefficient: float = 0.05
    model_version: str = "constituent-linear-v2.0.0"


class StrategyConfig(BaseModel):
    enabled_families: list[str] = Field(
        default_factory=lambda: [
            "LONG_CALL",
            "LONG_PUT",
            "CALL_DEBIT_SPREAD",
            "PUT_DEBIT_SPREAD",
            "BULL_PUT_CREDIT_SPREAD",
            "BEAR_CALL_CREDIT_SPREAD",
            "LONG_STRANGLE",
            "LONG_STRADDLE",
            "IRON_CONDOR",
            "CALL_BUTTERFLY",
            "PUT_BUTTERFLY",
        ]
    )
    min_open_interest: int = 25
    min_volume: int = 1
    max_relative_spread: float = 0.35
    min_credit: float = 0.10
    max_debit: float = 3.50
    max_width: float = 10.0
    min_edge_dollars: float = 0.03
    min_probability: float = 0.60
    max_candidates_per_cycle: int = 40


class RiskConfig(BaseModel):
    # Bounds are enforced rather than documented: these are the controls that
    # cap loss, and a typo that loads silently is the failure mode that matters.
    maximum_contracts: int = Field(default=1, ge=1, le=1)
    maximum_trades_per_day: int = Field(default=1, ge=0)
    maximum_trade_risk_dollars: float = Field(default=100.0, ge=0.0)
    account_risk_fraction: float = Field(default=0.0025, ge=0.0, le=1.0)
    daily_loss_limit_dollars: float = Field(default=200.0, ge=0.0)
    minimum_trust_to_trade: float = Field(default=0.75, ge=0.0, le=1.0)
    yellow_risk_multiplier: float = Field(default=0.50, ge=0.0, le=1.0)
    orange_risk_multiplier: float = Field(default=0.25, ge=0.0, le=1.0)
    forced_flat_time_et: str = "15:55"
    entry_start_time_et: str = "09:40"
    entry_stop_time_et: str = "15:15"
    block_on_incomplete_universe: bool = True
    block_on_stale_data: bool = True

    @field_validator("forced_flat_time_et", "entry_start_time_et", "entry_stop_time_et")
    @classmethod
    def _valid_et_time(cls, value: str) -> str:
        """Reject a time the risk controls cannot act on.

        Left unvalidated, "15:5x" or "24:00" only surfaces mid-session, when
        the control that reads it is the one being relied upon.
        """
        text = str(value).strip()
        try:
            hour_text, minute_text = text.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"expected a HH:MM Eastern time, got {value!r}") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"expected a HH:MM Eastern time, got {value!r}")
        # Normalise so any downstream string handling stays zero-padded.
        return f"{hour:02d}:{minute:02d}"


class TradingConfig(BaseModel):
    enabled: bool = False
    paper_mode: bool = True
    submit_orders: bool = False
    require_preview: bool = True
    limit_price_steps: int = 4
    limit_price_wait_seconds: int = 8
    cancel_confirm_seconds: int = 15
    tag_prefix: str = "SPYDER"


class AuditConfig(BaseModel):
    enabled: bool = True
    confirmation_interval_seconds: int = 30
    maturity_grace_seconds: int = 90
    maximum_snapshot_revision_bps: float = 1.0
    formal_anchor_minutes: int = 15
    minimum_promotion_sessions: int = 20
    minimum_promotion_forecasts: int = 500
    health_green: float = 0.75
    health_yellow: float = 0.50
    health_orange: float = 0.30


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8788
    mode: Literal["live", "demo"] = "live"
    require_view_token: bool = True
    view_token: SecretStr = SecretStr("")
    admin_token: SecretStr = SecretStr("")
    ingest_token: SecretStr = SecretStr("")
    websocket_interval_seconds: float = 1.0


class BackupConfig(BaseModel):
    enabled: bool = True
    remote: str = "gdrive:SPY Trading Backups/srv1575978"
    time_et: str = "17:00"
    keep_daily_database_snapshots: bool = True
    copy_raw_data: bool = True


class SuiteConfig(BaseModel):
    paths: PathsConfig = PathsConfig()
    tradier: TradierConfig = TradierConfig()
    universe: UniverseConfig = UniverseConfig()
    market: MarketConfig = MarketConfig()
    prediction: PredictionConfig = PredictionConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    trading: TradingConfig = TradingConfig()
    audit: AuditConfig = AuditConfig()
    dashboard: DashboardConfig = DashboardConfig()
    backup: BackupConfig = BackupConfig()

    @field_validator("trading")
    @classmethod
    def _paper_defaults(cls, value: TradingConfig) -> TradingConfig:
        if value.paper_mode:
            value.submit_orders = False
        return value

    def create_directories(self) -> None:
        directories = {
            self.paths.state_root,
            self.paths.database.parent,
            self.paths.dashboard_database.parent,
            self.paths.universe_cache.parent,
            self.paths.model_dir,
            self.paths.report_dir,
            self.paths.log_dir,
            self.paths.state_root / "market",
            self.paths.state_root / "candidates",
            self.paths.state_root / "audit",
            self.paths.state_root / "positions",
            self.paths.state_root / "backups",
        }
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def production_is_unlocked(self) -> bool:
        return self.paths.production_sentinel.is_file()

    def assert_live_trading_safe(self) -> None:
        if not self.trading.enabled or not self.trading.submit_orders:
            return
        problems: list[str] = []
        if self.tradier.environment != "production":
            problems.append("Tradier environment is not production")
        if self.trading.paper_mode:
            problems.append("paper_mode is still enabled")
        if not self.tradier.access_token.get_secret_value():
            problems.append("Tradier access token is missing")
        if not self.tradier.account_id:
            problems.append("Tradier account id is missing")
        if not self.production_is_unlocked():
            problems.append(f"production sentinel missing: {self.paths.production_sentinel}")
        if self.risk.maximum_contracts > 1:
            problems.append("maximum_contracts exceeds the v2.0.0 safety limit of one")
        if problems:
            raise RuntimeError("Live trading locked: " + "; ".join(problems))


def _merge_env(config: SuiteConfig) -> SuiteConfig:
    token = os.getenv("TRADIER_ACCESS_TOKEN")
    account = os.getenv("TRADIER_ACCOUNT_ID")
    env = os.getenv("TRADIER_ENVIRONMENT")
    if token is not None:
        config.tradier.access_token = SecretStr(token)
    if account is not None:
        config.tradier.account_id = account.strip()
    if env in {"sandbox", "production"}:
        config.tradier.environment = env
    for field, variable in (
        ("view_token", "SPY_DER_VIEW_TOKEN"),
        ("admin_token", "SPY_DER_ADMIN_TOKEN"),
        ("ingest_token", "SPY_DER_INGEST_TOKEN"),
    ):
        value = os.getenv(variable)
        if value is not None:
            setattr(config.dashboard, field, SecretStr(value))
    return config


def load_config(path: str | Path = "/etc/spy-der/config.yaml") -> SuiteConfig:
    path = Path(path)
    raw: dict = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    config = SuiteConfig.model_validate(raw)
    config = _merge_env(config)
    config.create_directories()
    config.assert_live_trading_safe()
    return config

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class PathsConfig(BaseModel):
    state_root: Path = Path("/var/lib/alpha-spy")
    database: Path = Path("/var/lib/alpha-spy/journal/alpha-spy.db")
    dashboard_database: Path = Path("/var/lib/alpha-spy/dashboard/command-center.sqlite")
    universe_cache: Path = Path("/var/lib/alpha-spy/reference/universe.csv")
    model_dir: Path = Path("/var/lib/alpha-spy/models")
    report_dir: Path = Path("/var/lib/alpha-spy/reports")
    log_dir: Path = Path("/var/log/alpha-spy")
    production_sentinel: Path = Path("/etc/alpha-spy/PRODUCTION_UNLOCKED")
    production_approval: Path = Path("/etc/alpha-spy/PRODUCTION_APPROVED.json")
    event_calendar: Path = Path("/etc/alpha-spy/events.json")
    promotion_report: Path = Path("/var/lib/alpha-spy/reports/promotion-latest.json")


class TradierConfig(BaseModel):
    # Execution connection. For the current Alpha-SPY deployment this is the
    # Tradier sandbox virtual account. Legacy field names are retained because
    # operational scripts and tests already depend on them.
    environment: Literal["sandbox", "production"] = "sandbox"
    access_token: SecretStr = SecretStr("")
    account_id: str = ""

    # Market-data connection. Alpha-SPY deliberately separates this from the
    # execution account so production real-time data can drive paper orders.
    market_environment: Literal["production"] = "production"
    market_access_token: SecretStr = SecretStr("")

    timeout_seconds: float = 20.0
    market_data_chunk_size: int = 150
    preview_orders: bool = True
    stream_enabled: bool = True

    @property
    def base_url(self) -> str:
        """Execution REST endpoint (sandbox for paper trading)."""
        return (
            "https://sandbox.tradier.com/v1"
            if self.environment == "sandbox"
            else "https://api.tradier.com/v1"
        )

    @property
    def market_base_url(self) -> str:
        """Real-time market-data REST endpoint."""
        return "https://api.tradier.com/v1"

    @property
    def stream_url(self) -> str:
        return "wss://ws.tradier.com/v1/markets/events"


class UniverseConfig(BaseModel):
    source: Literal["ssga_spy", "ishares_ivv", "local_csv", "fallback"] = "ssga_spy"
    source_url: str = (
        "https://www.ssga.com/us/en/institutional/library-content/products/"
        "fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
    )
    fallback_source_urls: list[str] = Field(
        default_factory=lambda: [
            "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/"
            "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund",
        ]
    )
    local_csv: Path = Path("/etc/alpha-spy/universe.csv")
    refresh_hour_et: int = 7
    maximum_age_hours: int = 36
    minimum_symbols: int = 450
    top_n_quotes: int = 503
    top_n_option_iv: int = 40
    minimum_covered_weight: float = 0.90


class MarketConfig(BaseModel):
    # REST polling remains a fallback/run-once path. The daemon path uses the
    # production Tradier websocket and persists a synchronized snapshot on this cadence.
    poll_interval_seconds: int = 60
    stream_snapshot_interval_seconds: int = Field(default=60, ge=1, le=60)
    stream_stale_seconds: int = Field(default=15, ge=3, le=120)
    option_chain_refresh_seconds: int = Field(default=30, ge=5, le=300)
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
    event_state_default: Literal[
        "ordinary", "earnings_heavy", "macro_announcement", "rebalance", "unknown"
    ] = "ordinary"


class ForecastHorizonSpec(BaseModel):
    name: str
    minutes: int = Field(ge=1)
    role: Literal["timing", "primary", "advisory", "research"] = "advisory"
    trade_eligible: bool = False
    cadence_minutes: int = Field(default=1, ge=1, le=60)


class PredictionConfig(BaseModel):
    # `horizon_minutes` remains the authoritative trade horizon for backwards
    # compatibility. The bundle below is the complete forecasting stack.
    horizon_minutes: int = 15
    horizons: list[ForecastHorizonSpec] = Field(
        default_factory=lambda: [
            ForecastHorizonSpec(name="5m", minutes=5, role="timing", cadence_minutes=1),
            ForecastHorizonSpec(name="15m", minutes=15, role="primary", trade_eligible=True, cadence_minutes=1),
            ForecastHorizonSpec(name="30m", minutes=30, role="primary", cadence_minutes=1),
            ForecastHorizonSpec(name="60m", minutes=60, role="advisory", cadence_minutes=5),
            ForecastHorizonSpec(name="120m", minutes=120, role="advisory", cadence_minutes=5),
            ForecastHorizonSpec(name="EOD", minutes=390, role="advisory", cadence_minutes=5),
            ForecastHorizonSpec(name="1D", minutes=390, role="research", cadence_minutes=15),
            ForecastHorizonSpec(name="5D", minutes=1950, role="research", cadence_minutes=15),
        ]
    )
    minimum_history_rows: int = 30
    return_lookback_rows: int = 240
    distribution_z: float = 1.28
    min_sigma_return: float = 0.00045
    max_sigma_return: float = 0.045
    # Hand-set coefficients are a cold-start bootstrap only.  Once enough matured
    # timestamp-valid observations exist, promotable horizons use a walk-forward
    # standardized Ridge fit.
    pressure_coefficient: float = 0.35
    residual_coefficient: float = 0.20
    breadth_coefficient: float = 0.08
    mean_reversion_coefficient: float = 0.05
    ridge_enabled: bool = True
    ridge_min_samples: int = Field(default=200, ge=30)
    ridge_lookback: int = Field(default=5000, ge=100, le=50000)
    ridge_alpha_by_horizon: dict[str, float] = Field(
        default_factory=lambda: {
            "5m": 1.0, "15m": 1.0, "30m": 0.00001, "60m": 1.0,
            "120m": 1.0, "EOD": 1.0, "1D": 1.0, "5D": 1.0,
        }
    )
    shadow_model_enabled: bool = True
    shadow_model_min_samples: int = Field(default=500, ge=100)
    shadow_model_lookback: int = Field(default=3000, ge=500, le=20000)
    shadow_model_max_iter: int = Field(default=80, ge=20, le=500)
    regime_calibration_min_samples: int = 30
    global_calibration_min_samples: int = 100
    calibration_lookback: int = 5000
    physical_paths: int = Field(default=6000, ge=1000, le=50000)
    risk_neutral_paths: int = Field(default=6000, ge=1000, le=50000)
    covariance_symbols: int = Field(default=120, ge=20, le=503)
    covariance_lookback_rows: int = Field(default=240, ge=30, le=2000)
    minimum_covariance_observations: int = Field(default=30, ge=10)
    minimum_pq_surface_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    max_model_uncertainty: float = Field(default=0.025, ge=0.0)
    model_version: str = "alpha-spy-full-decision-v3.0.0"


class ContextConfig(BaseModel):
    # Tradier does not provide futures; these are explicitly labelled liquid
    # cash-market proxies rather than silently pretending to be ES/NQ/RTY.
    futures_proxies: dict[str, str] = Field(
        default_factory=lambda: {"ES": "SPY", "NQ": "QQQ", "RTY": "IWM"}
    )
    credit_symbol: str = "HYG"
    dollar_symbol: str = "UUP"
    rates_symbols: list[str] = Field(default_factory=lambda: ["SHY", "IEF", "TLT"])
    volatility_symbols: list[str] = Field(default_factory=lambda: ["VIX", "VIX9D", "VIX3M"])
    sector_symbols: list[str] = Field(
        default_factory=lambda: [
            "XLC", "XLY", "XLP", "XLE", "XLF", "XLV",
            "XLI", "XLB", "XLRE", "XLK", "XLU",
        ]
    )
    require_credit_proxy: bool = True
    require_dollar_proxy: bool = False
    require_volatility_index: bool = False
    minimum_context_coverage: float = Field(default=0.75, ge=0.0, le=1.0)
    event_calendar_enabled: bool = True
    event_calendar_required: bool = True
    event_calendar_url: str = ""
    event_calendar_max_age_hours: int = Field(default=36, ge=1, le=168)
    event_pre_minutes: int = Field(default=30, ge=0, le=240)
    event_post_minutes: int = Field(default=30, ge=0, le=240)
    block_macro_event_entries: bool = True
    block_rebalance_entries: bool = True


class ValidationConfig(BaseModel):
    enabled: bool = True
    # One trade/day is the default safety scope. Requiring 60 actual sandbox
    # trades therefore forces roughly three calendar months of live-market paper
    # evidence before review is even possible.
    minimum_paper_sessions: int = Field(default=60, ge=1)
    minimum_matured_forecasts: int = Field(default=5000, ge=1)
    minimum_primary_horizon_samples: int = Field(default=750, ge=1)
    minimum_paper_trades: int = Field(default=60, ge=1)
    minimum_verified_data_fraction: float = Field(default=0.99, ge=0.0, le=1.0)
    minimum_trained_primary_fraction: float = Field(default=0.90, ge=0.0, le=1.0)
    minimum_direction_accuracy_15m: float = Field(default=0.53, ge=0.0, le=1.0)
    minimum_direction_wilson_lcb_15m: float = Field(default=0.50, ge=0.0, le=1.0)
    maximum_brier_15m: float = Field(default=0.245, ge=0.0, le=1.0)
    minimum_brier_skill_15m: float = Field(default=0.01, ge=-1.0, le=1.0)
    minimum_direction_accuracy_30m: float = Field(default=0.525, ge=0.0, le=1.0)
    minimum_direction_wilson_lcb_30m: float = Field(default=0.50, ge=0.0, le=1.0)
    maximum_brier_30m: float = Field(default=0.25, ge=0.0, le=1.0)
    minimum_brier_skill_30m: float = Field(default=0.0, ge=-1.0, le=1.0)
    minimum_interval_coverage: float = Field(default=0.72, ge=0.0, le=1.0)
    maximum_interval_coverage: float = Field(default=0.88, ge=0.0, le=1.0)
    confidence_level: float = Field(default=0.95, gt=0.50, lt=1.0)
    minimum_net_pnl: float = 0.0
    minimum_profit_factor: float = Field(default=1.15, ge=0.0)
    minimum_expectancy_lcb_dollars: float = 0.0
    maximum_drawdown_dollars: float = Field(default=600.0, ge=0.0)
    minimum_doubled_cost_pnl: float = 0.0
    recent_trade_window: int = Field(default=20, ge=5)
    minimum_recent_net_pnl: float = 0.0
    minimum_recent_profit_factor: float = Field(default=1.0, ge=0.0)
    maximum_mean_fill_slippage_dollars: float = Field(default=12.0, ge=0.0)
    minimum_broker_fill_fraction: float = Field(default=0.95, ge=0.0, le=1.0)
    maximum_reconciliation_blocks: int = Field(default=0, ge=0)
    maximum_realized_loss_to_model_ratio: float = Field(default=1.10, ge=1.0)
    minimum_regime_trades: int = Field(default=5, ge=1)
    minimum_tested_regimes: int = Field(default=3, ge=1)
    minimum_regime_coverage_fraction: float = Field(default=0.75, ge=0.0, le=1.0)
    require_nonnegative_regime_expectancy: bool = True
    require_replay_consistency: bool = True
    replay_sample_predictions: int = Field(default=60, ge=1, le=5000)

    @model_validator(mode="after")
    def _coverage_band(self) -> ValidationConfig:
        if self.maximum_interval_coverage < self.minimum_interval_coverage:
            raise ValueError("maximum_interval_coverage must be >= minimum_interval_coverage")
        return self


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
    assumed_expiry_time_et: str = "16:00"
    minimum_horizon_tenor_seconds: int = 1
    q_iv_floor: float = 0.01
    q_iv_cap: float = 5.0
    min_edge_to_uncertainty: float = Field(default=0.75, ge=0.0)
    require_positive_doubled_cost_ev: bool = True
    require_multi_horizon_alignment: bool = True

    @field_validator("assumed_expiry_time_et")
    @classmethod
    def _valid_expiry_time(cls, value: str) -> str:
        return _normalize_hhmm(value)


class RiskConfig(BaseModel):
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
    entry_grid_minutes: int = Field(default=5, ge=1, le=60)
    exit_monitor_interval_seconds: int = Field(default=60, ge=5, le=300)
    block_on_incomplete_universe: bool = True
    block_on_stale_data: bool = True
    require_verified_option_chain: bool = True
    require_verified_surface: bool = True
    minimum_surface_coverage: float = Field(default=0.70, ge=0.0, le=1.0)
    block_on_broker_reconciliation: bool = True

    @field_validator("forced_flat_time_et", "entry_start_time_et", "entry_stop_time_et")
    @classmethod
    def _valid_et_time(cls, value: str) -> str:
        return _normalize_hhmm(value)


class TradingConfig(BaseModel):
    enabled: bool = False
    paper_mode: bool = True
    submit_orders: bool = False
    require_preview: bool = True
    limit_price_steps: int = 4
    limit_price_wait_seconds: int = 8
    cancel_confirm_seconds: int = 15
    tag_prefix: str = "ALPHASPY"
    fee_per_contract: float = Field(default=0.65, ge=0.0)
    slippage_fraction_of_spread: float = Field(default=0.25, ge=0.0, le=2.0)
    minimum_slippage: float = Field(default=0.01, ge=0.0)
    force_market_exit_after_limit_failure: bool = True


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
    # Browser origins allowed to call the API cross-origin. Needed only when the
    # workstation is hosted off this machine (e.g. Vercel); empty means no
    # cross-origin access, which is correct for the loopback/SSH-tunnel default.
    allowed_origins: list[str] = []


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
    context: ContextConfig = ContextConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    trading: TradingConfig = TradingConfig()
    audit: AuditConfig = AuditConfig()
    validation: ValidationConfig = ValidationConfig()
    dashboard: DashboardConfig = DashboardConfig()
    backup: BackupConfig = BackupConfig()

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
            self.paths.state_root / "replay",
            self.paths.state_root / "validation",
            self.paths.state_root / "positions",
            self.paths.state_root / "backups",
        }
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def production_is_unlocked(self) -> bool:
        return self.paths.production_sentinel.is_file()

    def decision_fingerprint(self) -> str:
        """Hash every behavior-affecting input to forecasts, ranking, risk and fills.

        Validation thresholds are intentionally excluded here: changing a threshold
        should invalidate *promotion* but does not retroactively change a historical
        forecast. Secrets and machine-specific paths are excluded.
        """
        payload = {
            "universe": self.universe.model_dump(mode="json"),
            "market": self.market.model_dump(mode="json"),
            "data_transport": {
                "market_environment": self.tradier.market_environment,
                "stream_enabled": self.tradier.stream_enabled,
            },
            "prediction": self.prediction.model_dump(mode="json"),
            "context": self.context.model_dump(mode="json"),
            "strategy": self.strategy.model_dump(mode="json"),
            "risk": self.risk.model_dump(mode="json"),
            "trading_behavior": {
                "require_preview": self.trading.require_preview,
                "limit_price_steps": self.trading.limit_price_steps,
                "limit_price_wait_seconds": self.trading.limit_price_wait_seconds,
                "cancel_confirm_seconds": self.trading.cancel_confirm_seconds,
                "fee_per_contract": self.trading.fee_per_contract,
                "slippage_fraction_of_spread": self.trading.slippage_fraction_of_spread,
                "minimum_slippage": self.trading.minimum_slippage,
                "force_market_exit_after_limit_failure": self.trading.force_market_exit_after_limit_failure,
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    def production_fingerprint(self) -> str:
        """Bind live-review evidence to decision behavior *and* validation policy."""
        payload = {
            "decision_fingerprint": self.decision_fingerprint(),
            "validation": self.validation.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    def production_approval_status(self) -> tuple[bool, str]:
        path = self.paths.production_approval
        if not path.is_file():
            return False, f"production approval missing: {path}"
        try:
            approval = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return False, f"production approval unreadable: {exc}"
        if approval.get("approved") is not True:
            return False, "production approval does not contain approved=true"
        if str(approval.get("model_version") or "") != self.prediction.model_version:
            return False, "production approval model_version does not match runtime"
        if str(approval.get("config_fingerprint") or "") != self.production_fingerprint():
            return False, "production approval config_fingerprint does not match runtime"
        evidence_path = Path(str(approval.get("evidence_path") or ""))
        expected_sha = str(approval.get("evidence_sha256") or "").lower()
        if not evidence_path.is_file() or len(expected_sha) != 64:
            return False, "production approval evidence path/hash is invalid"
        evidence_bytes = evidence_path.read_bytes()
        actual_sha = hashlib.sha256(evidence_bytes).hexdigest()
        if actual_sha != expected_sha:
            return False, "production approval evidence hash does not match"
        try:
            evidence = json.loads(evidence_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False, "production approval evidence is not a validation JSON report"
        if evidence.get("status") != "ELIGIBLE_FOR_MANUAL_LIVE_REVIEW":
            return False, "production validation report has not passed all paper gates"
        if str(evidence.get("model_version") or "") != self.prediction.model_version:
            return False, "production validation model_version does not match runtime"
        if str(evidence.get("config_fingerprint") or "") != self.production_fingerprint():
            return False, "production validation config_fingerprint does not match runtime"
        if evidence.get("automatic_live_enable") is not False:
            return False, "production validation evidence has invalid live-enable semantics"
        return True, "approved"

    def assert_live_trading_safe(self) -> None:
        """Backward-compatible production-only safety check.

        Keep this API strict so existing operational tests and scripts retain their
        meaning. Broker sandbox submission uses assert_broker_submission_safe().
        """
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
            problems.append("maximum_contracts exceeds the safety limit of one")
        if problems:
            raise RuntimeError("Live trading locked: " + "; ".join(problems))

    def assert_broker_submission_safe(self) -> None:
        """Authorize broker submission while keeping market data and execution separate.

        Local simulation is `submit_orders=false`. With submission enabled:
        - sandbox requires `paper_mode=true`;
        - production requires `paper_mode=false`, the production sentinel, and
          an evidence-bound approval artifact.

        A production market-data token never authorizes order submission.
        """
        if not self.trading.enabled or not self.trading.submit_orders:
            return
        problems: list[str] = []
        if not self.tradier.market_access_token.get_secret_value():
            problems.append("Tradier production market-data access token is missing")
        if not self.tradier.stream_enabled:
            problems.append("Tradier production market-data stream is disabled")
        if not self.tradier.access_token.get_secret_value():
            problems.append("Tradier execution access token is missing")
        if not self.tradier.account_id:
            problems.append("Tradier execution account id is missing")
        if self.risk.maximum_contracts > 1:
            problems.append("maximum_contracts exceeds the safety limit of one")

        if self.tradier.environment == "sandbox":
            if not self.trading.paper_mode:
                problems.append("sandbox broker submission requires paper_mode=true")
        elif self.tradier.environment == "production":
            if self.trading.paper_mode:
                problems.append("production broker submission requires paper_mode=false")
            if not self.production_is_unlocked():
                problems.append(f"production sentinel missing: {self.paths.production_sentinel}")
            approved, approval_reason = self.production_approval_status()
            if not approved:
                problems.append(approval_reason)
        else:
            problems.append(f"unsupported Tradier execution environment: {self.tradier.environment}")

        if problems:
            raise RuntimeError("Broker submission locked: " + "; ".join(problems))


def _normalize_hhmm(value: str) -> str:
    text = str(value).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"expected a HH:MM Eastern time, got {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"expected a HH:MM Eastern time, got {value!r}")
    return f"{hour:02d}:{minute:02d}"


def _env_bool(name: str, current: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return current
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, got {value!r}")


def _merge_env(config: SuiteConfig) -> SuiteConfig:
    # Explicit split credentials. Never use the production market-data token as
    # an execution credential. Legacy names continue to map to execution only.
    execution_token = os.getenv("TRADIER_EXECUTION_ACCESS_TOKEN")
    if execution_token is None:
        execution_token = os.getenv("TRADIER_ACCESS_TOKEN")
    execution_account = os.getenv("TRADIER_EXECUTION_ACCOUNT_ID")
    if execution_account is None:
        execution_account = os.getenv("TRADIER_ACCOUNT_ID")
    execution_env = os.getenv("TRADIER_EXECUTION_ENVIRONMENT")
    if execution_env is None:
        execution_env = os.getenv("TRADIER_ENVIRONMENT")
    market_token = os.getenv("TRADIER_MARKET_ACCESS_TOKEN")

    if execution_token is not None:
        config.tradier.access_token = SecretStr(execution_token)
    if execution_account is not None:
        config.tradier.account_id = execution_account.strip()
    if execution_env in {"sandbox", "production"}:
        config.tradier.environment = execution_env
    if market_token is not None:
        config.tradier.market_access_token = SecretStr(market_token)

    config.trading.enabled = _env_bool("ALPHA_SPY_TRADING_ENABLED", config.trading.enabled)
    config.trading.submit_orders = _env_bool("ALPHA_SPY_SUBMIT_ORDERS", config.trading.submit_orders)
    config.trading.paper_mode = _env_bool("ALPHA_SPY_PAPER_MODE", config.trading.paper_mode)
    config.tradier.stream_enabled = _env_bool("TRADIER_STREAM_ENABLED", config.tradier.stream_enabled)

    for field, variable in (
        ("view_token", "ALPHA_SPY_VIEW_TOKEN"),
        ("admin_token", "ALPHA_SPY_ADMIN_TOKEN"),
        ("ingest_token", "ALPHA_SPY_INGEST_TOKEN"),
    ):
        value = os.getenv(variable)
        if value is not None:
            setattr(config.dashboard, field, SecretStr(value))
    return config


def load_config(path: str | Path = "/etc/alpha-spy/config.yaml") -> SuiteConfig:
    path = Path(path)
    raw: dict = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    config = SuiteConfig.model_validate(raw)
    config = _merge_env(config)
    config.create_directories()
    config.assert_broker_submission_safe()
    return config

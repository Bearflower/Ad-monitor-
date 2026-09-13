from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _dotenv_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ziniao_shopee_store_id: str = ""
    ziniao_shopee_store_name: str = "Shopee Store"
    daily_run_time: str = "09:00"
    timezone: str = "Asia/Shanghai"
    bridge_retries: int = 3
    bridge_retry_delay_seconds: int = 10
    page_ready_retries: int = 10
    page_ready_retry_delay_seconds: int = 1
    ziniao_cli_timeout_seconds: int = 30
    shopee_ads_max_pages: int = 20
    shopee_currency: str = "THB"
    history_days: int = 30
    no_data_recheck_days: int = 7
    backfill_claim_minutes: int = 15
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_max_body_bytes: int = 65_536
    web_nonce_ttl_seconds: int = 1800
    reconciliation_tolerance: Decimal = Decimal("0.01")
    short_window_days: int = 7
    long_window_days: int = 30
    default_budget_step: Decimal = Decimal("0.10")
    low_confidence_budget_step: Decimal = Decimal("0.05")
    max_budget_step: Decimal = Decimal("0.20")
    max_target_roas_step: Decimal = Decimal("0.20")
    min_orders: int = 3
    min_history_days: int = 3
    cooldown_days: int = 3
    evaluation_days: int = 7
    min_budget_utilization: Decimal = Decimal("0.80")
    floor_guard_band: Decimal = Decimal("0.10")
    severe_underperformance_ratio: Decimal = Decimal("0.70")
    structure_winner_share: Decimal = Decimal("0.70")
    structure_min_items: int = 2
    effectiveness_min_samples: int = 5
    default_growth_intensity: Decimal = Decimal("0.50")
    rollback_days: int = 3
    minimum_gmv_growth: Decimal = Decimal("0.05")
    budget_quantum: Decimal = Decimal("0.01")
    roas_quantum: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        for name in (
            "bridge_retries",
            "bridge_retry_delay_seconds",
            "page_ready_retries",
            "page_ready_retry_delay_seconds",
            "ziniao_cli_timeout_seconds",
            "shopee_ads_max_pages",
            "history_days",
            "no_data_recheck_days",
            "backfill_claim_minutes",
            "web_max_body_bytes",
            "web_nonce_ttl_seconds",
            "short_window_days",
            "long_window_days",
            "min_orders",
            "min_history_days",
            "evaluation_days",
            "structure_min_items",
            "effectiveness_min_samples",
            "rollback_days",
        ):
            if (
                not isinstance(getattr(self, name), int)
                or isinstance(getattr(self, name), bool)
                or getattr(self, name) <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")
        if (
            not isinstance(self.cooldown_days, int)
            or isinstance(self.cooldown_days, bool)
            or self.cooldown_days < 0
        ):
            raise ValueError("cooldown_days must be a non-negative integer")
        if self.long_window_days < self.short_window_days:
            raise ValueError("long_window_days must be at least short_window_days")
        if (
            not isinstance(self.web_port, int)
            or isinstance(self.web_port, bool)
            or not 1 <= self.web_port <= 65535
        ):
            raise ValueError("web_port must be between 1 and 65535")
        if self.web_host != "127.0.0.1":
            raise ValueError("web_host must be 127.0.0.1")
        for name in (
            "reconciliation_tolerance",
            "default_budget_step",
            "low_confidence_budget_step",
            "max_budget_step",
            "max_target_roas_step",
            "min_budget_utilization",
            "floor_guard_band",
            "severe_underperformance_ratio",
            "structure_winner_share",
            "default_growth_intensity",
            "minimum_gmv_growth",
            "budget_quantum",
            "roas_quantum",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be finite")
            maximum = Decimal(1) if name != "reconciliation_tolerance" else None
            if value < 0 or (maximum is not None and value > maximum):
                raise ValueError(
                    f"{name} must be between 0 and 1"
                    if maximum is not None
                    else f"{name} must be non-negative"
                )
        if (
            not self.low_confidence_budget_step
            <= self.default_budget_step
            <= self.max_budget_step
        ):
            raise ValueError(
                "low_confidence_budget_step must not exceed default_budget_step or max_budget_step"
            )
        if self.budget_quantum <= 0 or self.roas_quantum <= 0:
            raise ValueError("budget_quantum and roas_quantum must be positive")
        if not isinstance(
            self.shopee_currency, str
        ) or self.shopee_currency.upper() not in {"THB", "USD", "CNY"}:
            raise ValueError("shopee_currency must be THB, USD, or CNY")

    @classmethod
    def from_env(cls) -> Settings:
        dotenv = _dotenv_values(Path.cwd() / ".env")

        def value(name: str, default: str = "") -> str:
            return os.getenv(name, dotenv.get(name, default))

        daily_run_time = value("ADWATCH_DAILY_RUN_TIME", "09:00")
        _parse_daily_time(daily_run_time)
        timezone = value("ADWATCH_TIMEZONE", "Asia/Shanghai")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(
                "ADWATCH_TIMEZONE must name an installed IANA timezone"
            ) from error

        def positive_int(name: str, default: str) -> int:
            try:
                parsed = int(value(name, default))
            except ValueError as error:
                raise ValueError(f"{name} must be a positive integer") from error
            if parsed <= 0:
                raise ValueError(f"{name} must be a positive integer")
            return parsed

        def non_negative_int(name: str, default: str) -> int:
            try:
                parsed = int(value(name, default))
            except ValueError as error:
                raise ValueError(f"{name} must be a non-negative integer") from error
            if parsed < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            return parsed

        def decimal_between(
            name: str,
            default: str,
            *,
            minimum: Decimal,
            maximum: Decimal | None = None,
        ) -> Decimal:
            try:
                parsed = Decimal(value(name, default))
            except InvalidOperation as error:
                raise ValueError(f"{name} must be a decimal") from error
            if not parsed.is_finite():
                raise ValueError(f"{name} must be finite")
            if parsed < minimum or (maximum is not None and parsed > maximum):
                bounds = f">= {minimum}"
                if maximum is not None:
                    bounds = f"between {minimum} and {maximum}"
                raise ValueError(f"{name} must be {bounds}")
            return parsed

        history_days = positive_int("ADWATCH_HISTORY_DAYS", "30")
        no_data_recheck_days = positive_int("ADWATCH_NO_DATA_RECHECK_DAYS", "7")
        backfill_claim_minutes = positive_int("ADWATCH_BACKFILL_CLAIM_MINUTES", "15")
        web_host = value("ADWATCH_WEB_HOST", "127.0.0.1")
        if web_host != "127.0.0.1":
            raise ValueError("ADWATCH_WEB_HOST must be 127.0.0.1")
        web_port = positive_int("ADWATCH_WEB_PORT", "8765")
        if web_port > 65535:
            raise ValueError("ADWATCH_WEB_PORT must be between 1 and 65535")
        reconciliation_tolerance = decimal_between(
            "ADWATCH_RECONCILIATION_TOLERANCE",
            "0.01",
            minimum=Decimal(0),
        )
        short_window_days = positive_int("ADWATCH_SHORT_WINDOW_DAYS", "7")
        long_window_days = positive_int("ADWATCH_LONG_WINDOW_DAYS", "30")
        if long_window_days < short_window_days:
            raise ValueError(
                "ADWATCH_LONG_WINDOW_DAYS must be at least ADWATCH_SHORT_WINDOW_DAYS"
            )
        low_confidence_budget_step = decimal_between(
            "ADWATCH_LOW_CONFIDENCE_BUDGET_STEP",
            "0.05",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        default_budget_step = decimal_between(
            "ADWATCH_DEFAULT_BUDGET_STEP",
            "0.10",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        max_budget_step = decimal_between(
            "ADWATCH_MAX_BUDGET_STEP",
            "0.20",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        if not (low_confidence_budget_step <= default_budget_step <= max_budget_step):
            raise ValueError(
                "ADWATCH_LOW_CONFIDENCE_BUDGET_STEP must not exceed "
                "ADWATCH_DEFAULT_BUDGET_STEP or ADWATCH_MAX_BUDGET_STEP"
            )
        max_target_roas_step = decimal_between(
            "ADWATCH_MAX_TARGET_ROAS_STEP",
            "0.20",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        min_budget_utilization = decimal_between(
            "ADWATCH_MIN_BUDGET_UTILIZATION",
            "0.80",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        floor_guard_band = decimal_between(
            "ADWATCH_FLOOR_GUARD_BAND",
            "0.10",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        severe_underperformance_ratio = decimal_between(
            "ADWATCH_SEVERE_UNDERPERFORMANCE_RATIO",
            "0.70",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        structure_winner_share = decimal_between(
            "ADWATCH_STRUCTURE_WINNER_SHARE",
            "0.70",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        default_growth_intensity = decimal_between(
            "ADWATCH_DEFAULT_GROWTH_INTENSITY",
            "0.50",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        minimum_gmv_growth = decimal_between(
            "ADWATCH_MINIMUM_GMV_GROWTH",
            "0.05",
            minimum=Decimal(0),
            maximum=Decimal(1),
        )
        budget_quantum = decimal_between(
            "ADWATCH_BUDGET_QUANTUM", "0.01", minimum=Decimal(0)
        )
        roas_quantum = decimal_between(
            "ADWATCH_ROAS_QUANTUM", "0.01", minimum=Decimal(0)
        )
        if budget_quantum == 0 or roas_quantum == 0:
            raise ValueError(
                "ADWATCH_BUDGET_QUANTUM and ADWATCH_ROAS_QUANTUM must be positive"
            )

        return cls(
            data_dir=Path(value("ADWATCH_DATA_DIR", "var")).expanduser(),
            ziniao_shopee_store_id=value("ZINIAO_SHOPEE_STORE_ID"),
            ziniao_shopee_store_name=value("ZINIAO_SHOPEE_STORE_NAME", "Shopee Store"),
            daily_run_time=daily_run_time,
            timezone=timezone,
            bridge_retries=positive_int("ADWATCH_BRIDGE_RETRIES", "3"),
            bridge_retry_delay_seconds=positive_int(
                "ADWATCH_BRIDGE_RETRY_DELAY_SECONDS", "10"
            ),
            page_ready_retries=positive_int("ADWATCH_PAGE_READY_RETRIES", "10"),
            page_ready_retry_delay_seconds=positive_int(
                "ADWATCH_PAGE_READY_RETRY_DELAY_SECONDS", "1"
            ),
            ziniao_cli_timeout_seconds=positive_int(
                "ADWATCH_ZINIAO_CLI_TIMEOUT_SECONDS", "30"
            ),
            shopee_ads_max_pages=positive_int("ADWATCH_SHOPEE_ADS_MAX_PAGES", "20"),
            shopee_currency=value("ADWATCH_SHOPEE_CURRENCY", "THB").upper(),
            history_days=history_days,
            no_data_recheck_days=no_data_recheck_days,
            backfill_claim_minutes=backfill_claim_minutes,
            web_host=web_host,
            web_port=web_port,
            web_max_body_bytes=positive_int("ADWATCH_WEB_MAX_BODY_BYTES", "65536"),
            web_nonce_ttl_seconds=positive_int(
                "ADWATCH_WEB_NONCE_TTL_SECONDS", "1800"
            ),
            reconciliation_tolerance=reconciliation_tolerance,
            short_window_days=short_window_days,
            long_window_days=long_window_days,
            default_budget_step=default_budget_step,
            low_confidence_budget_step=low_confidence_budget_step,
            max_budget_step=max_budget_step,
            max_target_roas_step=max_target_roas_step,
            min_orders=positive_int("ADWATCH_MIN_ORDERS", "3"),
            min_history_days=positive_int("ADWATCH_MIN_HISTORY_DAYS", "3"),
            cooldown_days=non_negative_int("ADWATCH_COOLDOWN_DAYS", "3"),
            evaluation_days=positive_int("ADWATCH_EVALUATION_DAYS", "7"),
            min_budget_utilization=min_budget_utilization,
            floor_guard_band=floor_guard_band,
            severe_underperformance_ratio=severe_underperformance_ratio,
            structure_winner_share=structure_winner_share,
            structure_min_items=positive_int("ADWATCH_STRUCTURE_MIN_ITEMS", "2"),
            effectiveness_min_samples=positive_int(
                "ADWATCH_EFFECTIVENESS_MIN_SAMPLES", "5"
            ),
            default_growth_intensity=default_growth_intensity,
            rollback_days=positive_int("ADWATCH_ROLLBACK_DAYS", "3"),
            minimum_gmv_growth=minimum_gmv_growth,
            budget_quantum=budget_quantum,
            roas_quantum=roas_quantum,
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "adwatch.sqlite3"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def daily_run_hour(self) -> int:
        return _parse_daily_time(self.daily_run_time)[0]

    @property
    def daily_run_minute(self) -> int:
        return _parse_daily_time(self.daily_run_time)[1]

def _parse_daily_time(raw: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = raw.split(":", 1)
        if len(hour_text) != 2 or len(minute_text) != 2:
            raise ValueError
        hour, minute = int(hour_text), int(minute_text)
    except ValueError as error:
        raise ValueError("ADWATCH_DAILY_RUN_TIME must use HH:MM") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("ADWATCH_DAILY_RUN_TIME must use a valid 24-hour time")
    return hour, minute

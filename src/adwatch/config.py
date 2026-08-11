from __future__ import annotations

import os
from dataclasses import dataclass
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
    evidence_root: Path | None = None
    ziniao_company: str = ""
    ziniao_username: str = ""
    ziniao_password: str = ""
    ziniao_endpoint: str = ""
    ziniao_tiktok_store_id: str = ""
    ziniao_tiktok_store_name: str = "TikTok Store"
    ziniao_shopee_store_id: str = ""
    ziniao_shopee_store_name: str = "Shopee Store"
    feishu_webhook: str = ""
    feishu_callback_secret: str = ""
    feishu_callback_public_url: str = ""
    tiktok_api_oauth: str = ""
    shopee_api_oauth: str = ""
    tiktok_collection_enabled: bool = False
    live_writes: bool = False
    live_allowlist: frozenset[tuple[str, str, str]] = frozenset()
    daily_run_time: str = "09:00"
    timezone: str = "Asia/Shanghai"
    bridge_retries: int = 3
    bridge_retry_delay_seconds: int = 10
    exchange_rate_max_staleness_days: int = 7
    shopee_history_lookback_days: int = 3
    income_scan_root: Path | None = None
    openai_api_key: str = ""
    knowledge_model: str = "gpt-5.6-terra"
    knowledge_enrichment: bool = True
    knowledge_max_pages_per_run: int = 50
    knowledge_request_delay_ms: int = 1500

    @classmethod
    def from_env(cls) -> Settings:
        dotenv = _dotenv_values(Path.cwd() / ".env")

        def value(name: str, default: str = "") -> str:
            return os.getenv(name, dotenv.get(name, default))

        allowlist = frozenset(
            tuple(item.strip().split(":", 2))
            for item in value("ADWATCH_LIVE_ALLOWLIST").split(",")
            if len(item.strip().split(":", 2)) == 3
        )
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
            parsed = int(value(name, default))
            if parsed <= 0:
                raise ValueError(f"{name} must be a positive integer")
            return parsed

        return cls(
            data_dir=Path(value("ADWATCH_DATA_DIR", "var")).expanduser(),
            evidence_root=(
                Path(value("ADWATCH_EVIDENCE_ROOT")).expanduser()
                if value("ADWATCH_EVIDENCE_ROOT")
                else None
            ),
            ziniao_company=value("ZINIAO_COMPANY"),
            ziniao_username=value("ZINIAO_USERNAME"),
            ziniao_password=value("ZINIAO_PASSWORD"),
            ziniao_endpoint=value("ZINIAO_ENDPOINT"),
            ziniao_tiktok_store_id=value("ZINIAO_TIKTOK_STORE_ID"),
            ziniao_tiktok_store_name=value(
                "ZINIAO_TIKTOK_STORE_NAME", "TikTok Store"
            ),
            ziniao_shopee_store_id=value("ZINIAO_SHOPEE_STORE_ID"),
            ziniao_shopee_store_name=value(
                "ZINIAO_SHOPEE_STORE_NAME", "Shopee Store"
            ),
            feishu_webhook=value("FEISHU_WEBHOOK"),
            feishu_callback_secret=value("FEISHU_CALLBACK_SECRET"),
            feishu_callback_public_url=value("FEISHU_CALLBACK_PUBLIC_URL"),
            tiktok_api_oauth=value("TIKTOK_API_OAUTH"),
            shopee_api_oauth=value("SHOPEE_API_OAUTH"),
            tiktok_collection_enabled=value(
                "ADWATCH_TIKTOK_COLLECTION_ENABLED", "false"
            ).lower()
            in {"1", "true", "yes"},
            live_writes=value("ADWATCH_LIVE_WRITES").lower()
            in {"1", "true", "yes"},
            live_allowlist=allowlist,
            daily_run_time=daily_run_time,
            timezone=timezone,
            bridge_retries=positive_int("ADWATCH_BRIDGE_RETRIES", "3"),
            bridge_retry_delay_seconds=positive_int(
                "ADWATCH_BRIDGE_RETRY_DELAY_SECONDS", "10"
            ),
            exchange_rate_max_staleness_days=positive_int(
                "ADWATCH_EXCHANGE_RATE_MAX_STALENESS_DAYS", "7"
            ),
            shopee_history_lookback_days=positive_int(
                "ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS", "3"
            ),
            income_scan_root=Path(
                value(
                    "ADWATCH_INCOME_SCAN_ROOT",
                    str(
                        Path.home()
                        / "Library/Application Support/ziniaobrowserdatas"
                    ),
                )
            ).expanduser(),
            openai_api_key=value("OPENAI_API_KEY"),
            knowledge_model=value(
                "ADWATCH_KNOWLEDGE_MODEL", "gpt-5.6-terra"
            ),
            knowledge_enrichment=value(
                "ADWATCH_KNOWLEDGE_ENRICHMENT", "true"
            ).lower()
            in {"1", "true", "yes"},
            knowledge_max_pages_per_run=positive_int(
                "ADWATCH_KNOWLEDGE_MAX_PAGES_PER_RUN", "50"
            ),
            knowledge_request_delay_ms=positive_int(
                "ADWATCH_KNOWLEDGE_REQUEST_DELAY_MS", "1500"
            ),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "adwatch.sqlite3"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def evidence_dir(self) -> Path:
        return self.evidence_root or self.data_dir / "evidence"

    @property
    def daily_run_hour(self) -> int:
        return _parse_daily_time(self.daily_run_time)[0]

    @property
    def daily_run_minute(self) -> int:
        return _parse_daily_time(self.daily_run_time)[1]

    @property
    def ziniao_ready(self) -> bool:
        return all(
            (
                self.ziniao_company,
                self.ziniao_username,
                self.ziniao_password,
                self.ziniao_endpoint,
            )
        )

    @property
    def ziniao_cli_ready(self) -> bool:
        return bool(self.ziniao_shopee_store_id) and (
            not self.tiktok_collection_enabled
            or bool(self.ziniao_tiktok_store_id)
        )

    @property
    def enabled_ziniao_platforms(self) -> tuple[str, ...]:
        if self.tiktok_collection_enabled:
            return ("tiktok", "shopee")
        return ("shopee",)

    @property
    def feishu_callback_ready(self) -> bool:
        return bool(
            self.feishu_callback_secret and self.feishu_callback_public_url
        )

    @property
    def platform_api_oauth_ready(self) -> bool:
        return bool(self.tiktok_api_oauth and self.shopee_api_oauth)


def _parse_daily_time(raw: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = raw.split(":", 1)
        if len(hour_text) != 2 or len(minute_text) != 2:
            raise ValueError
        hour, minute = int(hour_text), int(minute_text)
    except ValueError as error:
        raise ValueError(
            "ADWATCH_DAILY_RUN_TIME must use HH:MM"
        ) from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("ADWATCH_DAILY_RUN_TIME must use a valid 24-hour time")
    return hour, minute

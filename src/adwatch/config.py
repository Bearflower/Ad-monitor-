from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    live_writes: bool = False
    live_allowlist: frozenset[tuple[str, str, str]] = frozenset()

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
        return cls(
            data_dir=Path(value("ADWATCH_DATA_DIR", "var")).expanduser(),
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
            live_writes=value("ADWATCH_LIVE_WRITES").lower()
            in {"1", "true", "yes"},
            live_allowlist=allowlist,
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "adwatch.sqlite3"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

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
        return bool(
            self.ziniao_tiktok_store_id and self.ziniao_shopee_store_id
        )

    @property
    def feishu_callback_ready(self) -> bool:
        return bool(
            self.feishu_callback_secret and self.feishu_callback_public_url
        )

    @property
    def platform_api_oauth_ready(self) -> bool:
        return bool(self.tiktok_api_oauth and self.shopee_api_oauth)

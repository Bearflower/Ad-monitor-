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

    @classmethod
    def from_env(cls) -> Settings:
        dotenv = _dotenv_values(Path.cwd() / ".env")

        def value(name: str, default: str = "") -> str:
            return os.getenv(name, dotenv.get(name, default))

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

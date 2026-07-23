from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ziniao_company: str = ""
    ziniao_username: str = ""
    ziniao_password: str = ""
    ziniao_endpoint: str = ""
    feishu_webhook: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(os.getenv("ADWATCH_DATA_DIR", "var")).expanduser(),
            ziniao_company=os.getenv("ZINIAO_COMPANY", ""),
            ziniao_username=os.getenv("ZINIAO_USERNAME", ""),
            ziniao_password=os.getenv("ZINIAO_PASSWORD", ""),
            ziniao_endpoint=os.getenv("ZINIAO_ENDPOINT", ""),
            feishu_webhook=os.getenv("FEISHU_WEBHOOK", ""),
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

from datetime import date

from adwatch.config import Settings
from adwatch.domain import DailyAdMetric, Platform


class ZiniaoNotConfigured(RuntimeError):
    pass


class ZiniaoCollector:
    source = "ziniao"

    def __init__(self, settings: Settings, platform: Platform) -> None:
        self.settings = settings
        self.platform = platform

    def collect(self, data_date: date) -> list[DailyAdMetric]:
        if not self.settings.ziniao_ready:
            raise ZiniaoNotConfigured(
                "Ziniao collection requires ZINIAO_COMPANY, ZINIAO_USERNAME, "
                "ZINIAO_PASSWORD and ZINIAO_ENDPOINT"
            )
        raise NotImplementedError(
            "Ziniao transport is delivered in the real-collector plan"
        )

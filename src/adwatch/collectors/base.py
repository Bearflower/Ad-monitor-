from datetime import date
from typing import Protocol

from adwatch.domain import DailyAdMetric, Platform


class Collector(Protocol):
    platform: Platform
    source: str

    def collect(self, data_date: date) -> list[DailyAdMetric]: ...

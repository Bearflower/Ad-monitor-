from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Platform(str, Enum):
    TIKTOK = "tiktok"
    SHOPEE = "shopee"


@dataclass(frozen=True)
class DailyAdMetric:
    platform: Platform
    store: str
    account_id: str
    campaign_id: str
    sku_id: str
    data_date: date
    currency: str
    spend: Decimal
    attributed_gmv: Decimal
    orders: int
    source: str

    @property
    def roas(self) -> Decimal | None:
        if self.spend == 0:
            return None
        return (self.attributed_gmv / self.spend).quantize(Decimal("0.0001"))

    @property
    def cpa(self) -> Decimal | None:
        if self.orders == 0:
            return None
        return (self.spend / self.orders).quantize(Decimal("0.0001"))

    @property
    def logical_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.platform.value,
            self.store,
            self.account_id,
            self.campaign_id,
            self.sku_id,
            self.data_date.isoformat(),
        )


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str
    severity: str


@dataclass(frozen=True)
class ValidatedMetric:
    metric: DailyAdMetric
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

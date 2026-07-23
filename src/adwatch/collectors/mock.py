import random
from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric, Platform


class MockCollector:
    source = "mock"

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def collect(self, data_date: date) -> list[DailyAdMetric]:
        randomizer = random.Random(
            f"{self.platform.value}:{data_date.isoformat()}"
        )
        if self.platform is Platform.TIKTOK:
            store, currency = "TikTok MY Store", "MYR"
        else:
            store, currency = "Shopee TH Store", "THB"

        metrics = []
        for index in range(1, 5):
            spend = Decimal(randomizer.randint(8_000, 25_000)) / 100
            multiplier = (
                Decimal("0.4")
                if index == 4
                else Decimal(randomizer.randint(180, 520)) / 100
            )
            gmv = (spend * multiplier).quantize(Decimal("0.01"))
            metrics.append(
                DailyAdMetric(
                    platform=self.platform,
                    store=store,
                    account_id=f"{self.platform.value}-account-1",
                    campaign_id=f"{self.platform.value}-campaign-{index}",
                    sku_id=f"SKU-{index:03d}",
                    data_date=data_date,
                    currency=currency,
                    spend=spend,
                    attributed_gmv=gmv,
                    orders=max(0, int(gmv / Decimal("35"))),
                    source=self.source,
                )
            )
        return metrics

from __future__ import annotations

from datetime import date
from decimal import Decimal

from adwatch.collectors.base import Collector
from adwatch.collectors.ziniao_client import (
    ZiniaoApiError,
    ZiniaoCliClient,
)
from adwatch.domain import DailyAdMetric, Platform


class FallbackCollector(Collector):
    """备用采集器：通过 Ziniao CLI 通用浏览器能力执行。

    当主采集器（ZiniaoCollector）连续失败达到阈值时，
    自动切换到此备用采集器。备用采集器使用更简单的策略，
    只获取概览数据，不尝试获取明细。
    """

    def __init__(self, platform: str) -> None:
        self._platform = Platform(platform)
        self._client = ZiniaoCliClient()

    @property
    def platform(self) -> Platform:
        return self._platform

    @property
    def source(self) -> str:
        return f"fallback-{self._platform.value}"

    def collect(self, data_date: date) -> list[DailyAdMetric]:
        """备用采集：只获取概览数据，不获取明细。"""
        if self._platform is Platform.SHOPEE:
            return self._collect_shopee(data_date)
        return self._collect_tiktok(data_date)

    def _collect_shopee(self, data_date: date) -> list[DailyAdMetric]:
        """备用采集 Shopee 概览数据。"""
        metrics: list[DailyAdMetric] = []
        store_ids = getattr(self._client, "_store_ids", None)
        if store_ids is None:
            return metrics
        for store_id in store_ids:
            try:
                row = self._client.navigate_and_exec(
                    store_id=store_id,
                    url_template="",
                    script="",
                    expected_url="",
                    require_nonempty=False,
                    params={"date": data_date.isoformat()},
                )
                if row and isinstance(row, dict):
                    metrics.append(
                        DailyAdMetric(
                            platform=self._platform,
                            store=row.get("store", "unknown"),
                            account_id=row.get(
                                "account_id", "fallback"
                            ),
                            campaign_id=row.get(
                                "campaign_id", "fallback"
                            ),
                            sku_id="__ALL__",
                            data_date=data_date,
                            currency=row.get("currency", "THB"),
                            spend=Decimal(
                                str(row.get("spend", 0))
                            ),
                            attributed_gmv=Decimal(
                                str(row.get("attributed_gmv", 0))
                            ),
                            orders=int(row.get("orders", 0)),
                            source=self.source,
                        )
                    )
            except (ZiniaoApiError, OSError):
                continue
        return metrics

    def _collect_tiktok(self, data_date: date) -> list[DailyAdMetric]:
        # TikTok 备用采集暂未实现
        return []
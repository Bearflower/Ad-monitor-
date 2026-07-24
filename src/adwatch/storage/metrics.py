import sqlite3
from collections.abc import Iterable

from adwatch.domain import DailyAdMetric


class MetricRepository:
    @staticmethod
    def delete_platform_day(
        connection: sqlite3.Connection, platform: str, data_date: str
    ) -> None:
        connection.execute(
            """
            DELETE FROM daily_ad_metrics
            WHERE platform=? AND data_date=?
            """,
            (platform, data_date),
        )

    @staticmethod
    def upsert_many(
        connection: sqlite3.Connection, metrics: Iterable[DailyAdMetric]
    ) -> int:
        rows = list(metrics)
        connection.executemany(
            """
            INSERT INTO daily_ad_metrics (
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, roas, cpa, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                platform, store, account_id, campaign_id, sku_id, data_date
            ) DO UPDATE SET
                currency = excluded.currency,
                spend = excluded.spend,
                attributed_gmv = excluded.attributed_gmv,
                orders = excluded.orders,
                roas = excluded.roas,
                cpa = excluded.cpa,
                source = excluded.source,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            [
                (
                    metric.platform.value,
                    metric.store,
                    metric.account_id,
                    metric.campaign_id,
                    metric.sku_id,
                    metric.data_date.isoformat(),
                    metric.currency,
                    str(metric.spend),
                    str(metric.attributed_gmv),
                    metric.orders,
                    None if metric.roas is None else str(metric.roas),
                    None if metric.cpa is None else str(metric.cpa),
                    metric.source,
                )
                for metric in rows
            ],
        )
        return len(rows)

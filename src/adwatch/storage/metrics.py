import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.domain import DailyAdMetric

OVERVIEW_SKU = "__ALL__"
DEFAULT_RECONCILIATION_THRESHOLD = Decimal("0.05")  # 5%


@dataclass(frozen=True)
class ReconciliationResult:
    status: str  # "matched", "exceeded", "no_overview"
    overview_spend: Decimal
    detail_spend: Decimal
    difference: Decimal
    threshold: Decimal
    details_to_quarantine: tuple[DailyAdMetric, ...]


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

    @staticmethod
    def reconcile_ad_metrics(
        metrics: Iterable[DailyAdMetric],
        *,
        threshold: Decimal = DEFAULT_RECONCILIATION_THRESHOLD,
    ) -> ReconciliationResult:
        """对账：概览 KPI vs 明细行合计。

        将 metrics 分为概览（sku_id="__ALL__"）和明细行。如果存在概览行，
        比较概览 spend 与明细行 spend 合计。差异超过 threshold 时，明细行
        应进入隔离区而非写入 daily_ad_metrics。

        返回 ReconciliationResult，包含对账状态和需隔离的明细行。
        """
        metrics_list = list(metrics)
        overview = [m for m in metrics_list if m.sku_id == OVERVIEW_SKU]
        details = [m for m in metrics_list if m.sku_id != OVERVIEW_SKU]

        if not overview:
            return ReconciliationResult(
                status="no_overview",
                overview_spend=Decimal(0),
                detail_spend=sum(m.spend for m in details),
                difference=Decimal(0),
                threshold=threshold,
                details_to_quarantine=(),
            )

        overview_spend = overview[0].spend
        detail_spend = sum(m.spend for m in details)
        difference = abs(overview_spend - detail_spend)

        if difference > threshold * overview_spend:
            return ReconciliationResult(
                status="exceeded",
                overview_spend=overview_spend,
                detail_spend=detail_spend,
                difference=difference,
                threshold=threshold,
                details_to_quarantine=tuple(details),
            )

        return ReconciliationResult(
            status="matched",
            overview_spend=overview_spend,
            detail_spend=detail_spend,
            difference=difference,
            threshold=threshold,
            details_to_quarantine=(),
        )

    @staticmethod
    def log_reconciliation(
        connection: sqlite3.Connection,
        *,
        platform: str,
        store: str,
        account_id: str,
        data_date: date,
        result: ReconciliationResult,
    ) -> None:
        connection.execute(
            """
            INSERT INTO ad_reconciliation_log(
                platform, store, account_id, data_date,
                overview_spend, detail_spend, difference, threshold,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                platform,
                store,
                account_id,
                data_date.isoformat(),
                str(result.overview_spend),
                str(result.detail_spend),
                str(result.difference),
                str(result.threshold),
                result.status,
                datetime.now(UTC).isoformat(),
            ),
        )

    @staticmethod
    def quarantine_metrics(
        connection: sqlite3.Connection,
        run_id: str,
        metrics: Iterable[DailyAdMetric],
        *,
        issues_json: str,
    ) -> int:
        """将明细行写入隔离表（quarantined_records）。

        返回写入的隔离行数。
        """
        import json

        rows = list(metrics)
        connection.executemany(
            """
            INSERT INTO quarantined_records(run_id, raw_json, issues_json)
            VALUES (?, ?, ?)
            """,
            [
                (
                    run_id,
                    json.dumps(
                        {
                            "platform": m.platform.value,
                            "store": m.store,
                            "account_id": m.account_id,
                            "campaign_id": m.campaign_id,
                            "sku_id": m.sku_id,
                            "data_date": m.data_date.isoformat(),
                            "currency": m.currency,
                            "spend": str(m.spend),
                            "attributed_gmv": str(m.attributed_gmv),
                            "orders": m.orders,
                            "source": m.source,
                        },
                        sort_keys=True,
                    ),
                    issues_json,
                )
                for m in rows
            ],
        )
        return len(rows)

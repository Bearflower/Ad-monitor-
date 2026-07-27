from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from adwatch.storage.db import Database


@dataclass(frozen=True)
class PlatformSummary:
    platform: str
    spend: Decimal
    gmv: Decimal
    orders: int
    roas: Decimal | None
    net_profit: Decimal | None


@dataclass(frozen=True)
class SkuPerformance:
    platform: str
    store: str
    sku_id: str
    campaign_id: str
    roas: Decimal | None
    net_profit: Decimal | None


@dataclass(frozen=True)
class DailySnapshot:
    data_date: date
    platforms: tuple[PlatformSummary, ...]
    sku_performance: tuple[SkuPerformance, ...]
    alerts: tuple[dict[str, str], ...]
    recommendations: tuple[dict[str, str], ...]
    capabilities: dict[str, str]


class ReportReadModel:
    def __init__(self, database: Database) -> None:
        self.database = database

    def daily(self, data_date: date) -> DailySnapshot:
        day = data_date.isoformat()
        with self.database.connect() as connection:
            platform_rows = connection.execute(
                """
                SELECT m.platform, SUM(CAST(m.spend AS REAL)) spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) gmv,
                       SUM(m.orders) orders,
                       SUM(CAST(p.net_profit_cny AS REAL)) net_profit,
                       COUNT(p.net_profit_cny) profit_count,
                       COUNT(m.id) metric_count
                FROM daily_ad_metrics m
                LEFT JOIN profit_results p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                WHERE m.data_date=?
                GROUP BY m.platform
                ORDER BY m.platform
                """,
                (day,),
            ).fetchall()
            platforms = []
            for row in platform_rows:
                spend = Decimal(str(row["spend"] or 0))
                gmv = Decimal(str(row["gmv"] or 0))
                platforms.append(
                    PlatformSummary(
                        platform=row["platform"],
                        spend=spend.quantize(Decimal("0.01")),
                        gmv=gmv.quantize(Decimal("0.01")),
                        orders=int(row["orders"] or 0),
                        roas=(
                            None
                            if spend == 0
                            else (gmv / spend).quantize(Decimal("0.0001"))
                        ),
                        net_profit=(
                            None
                            if row["profit_count"] < row["metric_count"]
                            else Decimal(str(row["net_profit"])).quantize(
                                Decimal("0.01")
                            )
                        ),
                    )
                )
            sku_rows = connection.execute(
                """
                SELECT m.platform, m.store, m.sku_id, m.campaign_id, m.roas,
                       p.net_profit_cny
                FROM daily_ad_metrics m
                LEFT JOIN profit_results p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                WHERE m.data_date=?
                ORDER BY CAST(m.roas AS REAL) DESC
                """,
                (day,),
            ).fetchall()
            alerts = tuple(
                dict(row)
                for row in connection.execute(
                    """
                    SELECT rule_code, platform, campaign_id, sku_id,
                           severity, message, status
                    FROM alerts WHERE data_date=? ORDER BY severity, rule_code
                    """,
                    (day,),
                ).fetchall()
            )
            recommendations = tuple(
                dict(row)
                for row in connection.execute(
                    """
                    SELECT rule_code, platform, campaign_id, sku_id, action,
                           reason, status
                    FROM recommendations
                    WHERE data_date=? ORDER BY platform, campaign_id
                    """,
                    (day,),
                ).fetchall()
            )
            capability_row = connection.execute(
                """
                SELECT COUNT(m.id) metric_count,
                       COUNT(c.product_cost) cost_count,
                       COUNT(p.net_profit_cny) profit_count,
                       COUNT(
                           CASE
                             WHEN i.units IS NOT NULL
                              AND CAST(i.expected_daily_units AS REAL) > 0
                             THEN 1
                           END
                       ) inventory_count
                FROM daily_ad_metrics m
                LEFT JOIN product_costs c
                  ON c.sku_id=m.sku_id AND c.effective_date=(
                    SELECT MAX(c2.effective_date)
                    FROM product_costs c2
                    WHERE c2.sku_id=m.sku_id
                      AND c2.effective_date<=m.data_date
                  )
                LEFT JOIN profit_results p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                LEFT JOIN inventory_snapshots i
                  ON i.sku_id=m.sku_id AND i.snapshot_date=m.data_date
                WHERE m.data_date=?
                """,
                (day,),
            ).fetchone()
            metric_count = int(capability_row["metric_count"])
            estimated_ready = metric_count > 0 and int(
                capability_row["cost_count"]
            ) == metric_count
            verified_ready = metric_count > 0 and int(
                capability_row["profit_count"]
            ) == metric_count
            inventory_ready = verified_ready and int(
                capability_row["inventory_count"]
            ) == metric_count
        return DailySnapshot(
            data_date=data_date,
            platforms=tuple(platforms),
            sku_performance=tuple(
                SkuPerformance(
                    platform=row["platform"],
                    store=row["store"],
                    sku_id=row["sku_id"],
                    campaign_id=row["campaign_id"],
                    roas=None if row["roas"] is None else Decimal(row["roas"]),
                    net_profit=(
                        None
                        if row["net_profit_cny"] is None
                        else Decimal(row["net_profit_cny"])
                    ),
                )
                for row in sku_rows
            ),
            alerts=alerts,
            recommendations=recommendations,
            capabilities={
                "platform_metrics": (
                    "ready" if metric_count else "pending_data"
                ),
                "estimated_profit": (
                    "ready" if estimated_ready else "pending_data"
                ),
                "verified_profit": (
                    "ready" if verified_ready else "pending_data"
                ),
                "inventory_safe_strategy": (
                    "ready" if inventory_ready else "pending_data"
                ),
            },
        )

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from adwatch.reporting.break_even import BreakEvenTarget, calculate_break_even
from adwatch.storage.db import Database


@dataclass(frozen=True)
class PlatformSummary:
    platform: str
    spend: Decimal
    gmv: Decimal
    orders: int
    roas: Decimal | None
    net_profit: Decimal | None
    attributed_sales_cny: Decimal | None
    platform_fee_cny: Decimal | None
    ad_spend_cny: Decimal | None
    sku_and_other_cost_cny: Decimal | None
    break_even_target: BreakEvenTarget | None = None


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


@dataclass(frozen=True)
class TrendPoint:
    data_date: date
    spend: Decimal
    gmv: Decimal
    roas: Decimal | None


@dataclass(frozen=True)
class DashboardSnapshot:
    daily: DailySnapshot
    trends: dict[int, tuple[TrendPoint, ...]]
    collection_runs: tuple[dict[str, object], ...]
    approval_counts: dict[str, int]
    execution_counts: dict[str, int]


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
                       SUM(CAST(p.net_sales_cny AS REAL))
                           attributed_sales_cny,
                       SUM(CAST(p.platform_commission_cny AS REAL))
                           platform_fee_cny,
                       SUM(
                           CAST(m.spend AS REAL)
                           * CAST(r.rate_to_cny AS REAL)
                       ) ad_spend_cny,
                       SUM(CAST(p.net_profit_cny AS REAL)) net_profit,
                       COUNT(p.net_profit_cny) profit_count,
                       COUNT(r.rate_to_cny) rate_count,
                       COUNT(m.id) metric_count
                FROM daily_ad_metrics m
                LEFT JOIN profit_results p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                LEFT JOIN exchange_rates r
                  ON r.currency=m.currency AND r.rate_date=m.data_date
                WHERE m.data_date=?
                GROUP BY m.platform
                ORDER BY m.platform
                """,
                (day,),
            ).fetchall()
            matched_cost_orders = {
                row["platform"]: int(row["matched_orders"])
                for row in connection.execute(
                    """
                    SELECT orders.platform,
                           COUNT(DISTINCT orders.store || ':' || orders.order_id)
                               AS matched_orders
                    FROM platform_order_lines AS orders
                    WHERE substr(orders.ordered_at, 1, 10)=?
                      AND EXISTS (
                          SELECT 1
                          FROM order_cost_snapshots AS costs
                          WHERE costs.platform=orders.platform
                            AND costs.store=orders.store
                            AND costs.order_id=orders.order_id
                            AND costs.seller_sku=orders.seller_sku
                            AND costs.status='confirmed'
                      )
                    GROUP BY orders.platform
                    """,
                    (day,),
                ).fetchall()
            }
            platforms = []
            for row in platform_rows:
                spend = Decimal(str(row["spend"] or 0))
                gmv = Decimal(str(row["gmv"] or 0))
                breakdown_ready = (
                    row["profit_count"] == row["metric_count"]
                    and row["rate_count"] == row["metric_count"]
                )
                sales_cny = (
                    Decimal(str(row["attributed_sales_cny"])).quantize(
                        Decimal("0.01")
                    )
                    if breakdown_ready
                    else None
                )
                platform_fee_cny = (
                    Decimal(str(row["platform_fee_cny"])).quantize(
                        Decimal("0.01")
                    )
                    if breakdown_ready
                    else None
                )
                ad_spend_cny = (
                    Decimal(str(row["ad_spend_cny"])).quantize(
                        Decimal("0.01")
                    )
                    if breakdown_ready
                    else None
                )
                net_profit = (
                    Decimal(str(row["net_profit"])).quantize(
                        Decimal("0.01")
                    )
                    if breakdown_ready
                    else None
                )
                variable_cost_cny = (
                    None
                    if not breakdown_ready
                    else (
                        sales_cny
                        - platform_fee_cny
                        - ad_spend_cny
                        - net_profit
                    ).quantize(Decimal("0.01"))
                )
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
                        net_profit=net_profit,
                        attributed_sales_cny=sales_cny,
                        platform_fee_cny=platform_fee_cny,
                        ad_spend_cny=ad_spend_cny,
                        sku_and_other_cost_cny=variable_cost_cny,
                        break_even_target=calculate_break_even(
                            spend=spend,
                            gmv=gmv,
                            orders=int(row["orders"] or 0),
                            attributed_sales_cny=sales_cny,
                            platform_fee_cny=platform_fee_cny,
                            variable_cost_cny=variable_cost_cny,
                            matched_cost_orders=matched_cost_orders.get(
                                row["platform"], 0
                            ),
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

    def dashboard(self, data_date: date) -> DashboardSnapshot:
        start = data_date - timedelta(days=29)
        with self.database.connect() as connection:
            trend_rows = connection.execute(
                """
                SELECT data_date,
                       SUM(CAST(spend AS REAL)) spend,
                       SUM(CAST(attributed_gmv AS REAL)) gmv
                FROM daily_ad_metrics
                WHERE data_date BETWEEN ? AND ?
                GROUP BY data_date
                ORDER BY data_date
                """,
                (start.isoformat(), data_date.isoformat()),
            ).fetchall()
            points = tuple(
                TrendPoint(
                    data_date=date.fromisoformat(row["data_date"]),
                    spend=Decimal(str(row["spend"] or 0)),
                    gmv=Decimal(str(row["gmv"] or 0)),
                    roas=(
                        None
                        if not row["spend"]
                        else Decimal(str(row["gmv"] / row["spend"]))
                    ),
                )
                for row in trend_rows
            )
            collection_runs = tuple(
                dict(row)
                for row in connection.execute(
                    """
                    SELECT mode, platform, status, received_count,
                           accepted_count, quarantined_count, finished_at,
                           error_message
                    FROM collection_runs
                    ORDER BY started_at DESC LIMIT 10
                    """
                ).fetchall()
            )
            approval_counts = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) count
                    FROM approvals GROUP BY status
                    """
                )
            }
            execution_counts = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) count
                    FROM execution_audits GROUP BY status
                    """
                )
            }
        return DashboardSnapshot(
            daily=self.daily(data_date),
            trends={
                days: tuple(
                    point
                    for point in points
                    if point.data_date
                    >= data_date - timedelta(days=days - 1)
                )
                for days in (7, 14, 30)
            },
            collection_runs=collection_runs,
            approval_counts=approval_counts,
            execution_counts=execution_counts,
        )

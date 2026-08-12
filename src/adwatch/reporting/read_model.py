from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from adwatch.reporting.break_even import (
    BreakEvenTarget,
    calculate_break_even,
)
from adwatch.storage.db import Database

PROFIT_INPUT_NAMES = (
    "gmv",
    "refunds",
    "commission_rate",
    "product_cost",
    "ad_spend",
    "seller_shipping",
    "coupons",
    "allocated_fixed_cost",
    "exchange_rate",
)
READY_LINEAGE_STATUSES = frozenset({"ready", "carried_forward"})
_VALIDATED_PROFITS_CTE = """
validated_profits AS (
    SELECT profit.*
    FROM profit_results AS profit
    WHERE 9 = (
        SELECT COUNT(*)
        FROM profit_input_lineage AS lineage
        WHERE lineage.platform=profit.platform
          AND lineage.store=profit.store
          AND lineage.account_id=profit.account_id
          AND lineage.campaign_id=profit.campaign_id
          AND lineage.sku_id=profit.sku_id
          AND lineage.data_date=profit.data_date
          AND lineage.status IN ('ready', 'carried_forward')
    )
),
effective_metrics AS (
    SELECT metric.*
    FROM daily_ad_metrics AS metric
    WHERE metric.sku_id='__ALL__'
       OR NOT EXISTS (
           SELECT 1
           FROM daily_ad_metrics AS overview
           WHERE overview.platform=metric.platform
             AND overview.store=metric.store
             AND overview.account_id=metric.account_id
             AND overview.data_date=metric.data_date
             AND overview.sku_id='__ALL__'
       )
)
"""


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
class FeeRateSummary:
    platform: str
    store: str
    effective_rate: Decimal | None
    period_start: date | None
    period_end: date | None
    selection: str
    source_name: str | None
    source_sha256: str | None


@dataclass(frozen=True)
class ProfitInputLineageView:
    platform: str
    store: str
    input_name: str
    status: str
    source_type: str
    source_date: date | None
    age_days: int | None
    source_ref: str
    detail_code: str
    covered_rows: int
    total_rows: int


@dataclass(frozen=True)
class ProfitSourceSummary:
    platform: str
    store: str
    metric_rows: int
    attributed_orders: int
    platform_orders: int
    confirmed_cost_orders: int
    refund_observations: int
    refund_amount_observations: int
    inventory_snapshot_date: date | None
    exchange_source_date: date | None
    exchange_resolution_status: str
    fee_period_start: date | None
    fee_period_end: date | None
    fee_selection: str
    profit_status: str
    missing_inputs: tuple[str, ...]


@dataclass(frozen=True)
class DailySnapshot:
    data_date: date
    platforms: tuple[PlatformSummary, ...]
    sku_performance: tuple[SkuPerformance, ...]
    alerts: tuple[dict[str, str], ...]
    recommendations: tuple[dict[str, str], ...]
    capabilities: dict[str, str]
    fee_rates: tuple[FeeRateSummary, ...] = ()
    profit_sources: tuple[ProfitSourceSummary, ...] = ()
    profit_input_lineage: tuple[ProfitInputLineageView, ...] = ()


@dataclass(frozen=True)
class TrendPoint:
    data_date: date
    spend: Decimal
    gmv: Decimal
    roas: Decimal | None
    net_profit: Decimal | None


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

    def latest_data_date(self) -> date | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT MAX(data_date) AS data_date FROM daily_ad_metrics"
            ).fetchone()
        return (
            None
            if row is None or row["data_date"] is None
            else date.fromisoformat(row["data_date"])
        )

    def is_simulated(self, data_date: date) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS metric_count,
                       SUM(CASE WHEN source='mock' THEN 1 ELSE 0 END)
                           AS mock_count
                FROM daily_ad_metrics
                WHERE data_date=?
                """,
                (data_date.isoformat(),),
            ).fetchone()
        metric_count = int(row["metric_count"] or 0)
        return metric_count > 0 and int(row["mock_count"] or 0) == metric_count

    def daily(self, data_date: date) -> DailySnapshot:
        day = data_date.isoformat()
        with self.database.connect() as connection:
            platform_rows = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
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
                FROM effective_metrics m
                LEFT JOIN validated_profits p
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
                    WITH order_scope AS (
                        SELECT orders.platform,
                               COALESCE(alias.canonical_store, orders.store)
                                   AS store,
                               orders.order_id, orders.seller_sku
                        FROM effective_platform_order_lines AS orders
                        LEFT JOIN store_aliases AS alias
                          ON alias.platform=orders.platform
                         AND alias.source_store=orders.store
                        WHERE orders.resolution_status='ready'
                          AND substr(orders.ordered_at, 1, 10)=?
                          AND LOWER(TRIM(COALESCE(
                              orders.order_status, ''
                          ))) NOT IN ('cancelled', 'canceled')
                        GROUP BY orders.platform, store,
                                 orders.order_id, orders.seller_sku
                    ), cost_scope AS (
                        SELECT costs.platform,
                               COALESCE(alias.canonical_store, costs.store)
                                   AS store,
                               costs.order_id, costs.seller_sku
                        FROM order_cost_snapshots AS costs
                        LEFT JOIN store_aliases AS alias
                          ON alias.platform=costs.platform
                         AND alias.source_store=costs.store
                        WHERE costs.status='confirmed'
                        GROUP BY costs.platform, store,
                                 costs.order_id, costs.seller_sku
                    )
                    SELECT orders.platform,
                           COUNT(DISTINCT orders.store || ':'
                               || orders.order_id) AS matched_orders
                    FROM order_scope AS orders
                    WHERE NOT EXISTS (
                        SELECT 1 FROM order_scope AS missing
                        WHERE missing.platform=orders.platform
                          AND missing.store=orders.store
                          AND missing.order_id=orders.order_id
                          AND NOT EXISTS (
                              SELECT 1 FROM cost_scope AS costs
                              WHERE costs.platform=missing.platform
                                AND costs.store=missing.store
                                AND costs.order_id=missing.order_id
                                AND costs.seller_sku=missing.seller_sku
                          )
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
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT m.platform, m.store, m.sku_id, m.campaign_id, m.roas,
                       p.net_profit_cny
                FROM daily_ad_metrics m
                LEFT JOIN validated_profits p
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
            fee_rows = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}, metric_stores AS (
                    SELECT DISTINCT platform, store
                    FROM daily_ad_metrics WHERE data_date=?
                ), fee_lineage AS (
                    SELECT DISTINCT platform, store, fee_effective_rate,
                                    fee_period_start, fee_period_end,
                                    fee_selection, fee_source_name,
                                    fee_source_sha256
                    FROM validated_profits
                    WHERE data_date=? AND fee_effective_rate IS NOT NULL
                )
                SELECT stores.platform, stores.store,
                       lineage.fee_effective_rate,
                       lineage.fee_period_start, lineage.fee_period_end,
                       COALESCE(lineage.fee_selection, 'pending_data')
                           AS fee_selection,
                       lineage.fee_source_name, lineage.fee_source_sha256
                FROM metric_stores AS stores
                LEFT JOIN fee_lineage AS lineage
                  ON lineage.platform=stores.platform
                 AND lineage.store=stores.store
                ORDER BY stores.platform, stores.store,
                         lineage.fee_effective_rate
                """,
                (day, day),
            ).fetchall()
            profit_sources, input_lineage, capabilities = self._source_views(
                connection, day, fee_rows
            )
            if capabilities["inventory_safe_strategy"] != "ready":
                recommendations = ()
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
            capabilities=capabilities,
            fee_rates=tuple(
                FeeRateSummary(
                    platform=row["platform"],
                    store=row["store"],
                    effective_rate=(
                        None
                        if row["fee_effective_rate"] is None
                        else Decimal(row["fee_effective_rate"])
                    ),
                    period_start=(
                        None
                        if row["fee_period_start"] is None
                        else date.fromisoformat(row["fee_period_start"])
                    ),
                    period_end=(
                        None
                        if row["fee_period_end"] is None
                        else date.fromisoformat(row["fee_period_end"])
                    ),
                    selection=row["fee_selection"],
                    source_name=row["fee_source_name"],
                    source_sha256=row["fee_source_sha256"],
                )
                for row in fee_rows
            ),
            profit_sources=profit_sources,
            profit_input_lineage=input_lineage,
        )

    @staticmethod
    def _source_views(connection, day: str, fee_rows):
        metric_rows = connection.execute(
            """
            SELECT platform, store, COUNT(*) AS metric_rows,
                   SUM(orders) AS attributed_orders
            FROM daily_ad_metrics
            WHERE data_date=?
            GROUP BY platform, store
            ORDER BY platform, store
            """,
            (day,),
        ).fetchall()
        metric_counts = {
            (row["platform"], row["store"]): int(row["metric_rows"])
            for row in metric_rows
        }

        order_counts = _keyed_counts(
            connection.execute(
                """
                SELECT orders.platform,
                       COALESCE(alias.canonical_store, orders.store) AS store,
                       COUNT(DISTINCT orders.order_id) AS item_count
                FROM effective_platform_order_lines AS orders
                LEFT JOIN store_aliases AS alias
                  ON alias.platform=orders.platform
                 AND alias.source_store=orders.store
                WHERE orders.resolution_status='ready'
                  AND substr(orders.ordered_at, 1, 10)=?
                  AND LOWER(TRIM(COALESCE(orders.order_status, '')))
                      NOT IN ('cancelled', 'canceled')
                GROUP BY orders.platform, store
                """,
                (day,),
            ).fetchall()
        )
        cost_counts = _keyed_counts(
            connection.execute(
                """
                WITH order_scope AS (
                    SELECT orders.platform,
                           COALESCE(alias.canonical_store, orders.store)
                               AS store,
                           orders.order_id, orders.seller_sku
                    FROM effective_platform_order_lines AS orders
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=orders.platform
                     AND alias.source_store=orders.store
                    WHERE orders.resolution_status='ready'
                      AND substr(orders.ordered_at, 1, 10)=?
                      AND LOWER(TRIM(COALESCE(orders.order_status, '')))
                          NOT IN ('cancelled', 'canceled')
                    GROUP BY orders.platform, store,
                             orders.order_id, orders.seller_sku
                ), cost_scope AS (
                    SELECT costs.platform,
                           COALESCE(alias.canonical_store, costs.store)
                               AS store,
                           costs.order_id, costs.seller_sku
                    FROM order_cost_snapshots AS costs
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=costs.platform
                     AND alias.source_store=costs.store
                    WHERE costs.status='confirmed'
                    GROUP BY costs.platform, store,
                             costs.order_id, costs.seller_sku
                )
                SELECT orders.platform, orders.store,
                       COUNT(DISTINCT orders.order_id) AS item_count
                FROM order_scope AS orders
                WHERE NOT EXISTS (
                    SELECT 1 FROM order_scope AS missing
                    WHERE missing.platform=orders.platform
                      AND missing.store=orders.store
                      AND missing.order_id=orders.order_id
                      AND NOT EXISTS (
                          SELECT 1 FROM cost_scope AS costs
                          WHERE costs.platform=missing.platform
                            AND costs.store=missing.store
                            AND costs.order_id=missing.order_id
                            AND costs.seller_sku=missing.seller_sku
                      )
                )
                GROUP BY orders.platform, orders.store
                """,
                (day,),
            ).fetchall()
        )
        refund_rows = connection.execute(
            """
            SELECT refunds.platform,
                   COALESCE(alias.canonical_store, refunds.store) AS store,
                   COUNT(DISTINCT refunds.order_id) AS observation_count,
                   COUNT(DISTINCT CASE
                       WHEN refunds.amount_original IS NOT NULL
                       THEN refunds.order_id END
                   ) AS amount_count
            FROM platform_refund_observations AS refunds
            LEFT JOIN store_aliases AS alias
              ON alias.platform=refunds.platform
             AND alias.source_store=refunds.store
            WHERE refunds.order_date=?
            GROUP BY refunds.platform, store
            """,
            (day,),
        ).fetchall()
        refund_counts = {
            (row["platform"], row["store"]): (
                int(row["observation_count"]),
                int(row["amount_count"]),
            )
            for row in refund_rows
        }
        inventory_dates = {
            (row["platform"], row["store"]): date.fromisoformat(
                row["snapshot_date"]
            )
            for row in connection.execute(
                """
                SELECT inventory.platform,
                       COALESCE(alias.canonical_store, inventory.store) AS store,
                       MAX(inventory.snapshot_date) AS snapshot_date
                FROM platform_inventory_snapshots AS inventory
                LEFT JOIN store_aliases AS alias
                  ON alias.platform=inventory.platform
                 AND alias.source_store=inventory.store
                WHERE inventory.snapshot_date<=?
                GROUP BY inventory.platform, store
                """,
                (day,),
            ).fetchall()
        }
        inventory_strategy_rows = connection.execute(
                """
                WITH inventory_scope AS (
                    SELECT inventory.platform,
                           COALESCE(alias.canonical_store, inventory.store)
                               AS store,
                           inventory.seller_sku
                    FROM platform_inventory_snapshots AS inventory
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=inventory.platform
                     AND alias.source_store=inventory.store
                    WHERE inventory.snapshot_date=?
                    GROUP BY inventory.platform, store, inventory.seller_sku
                ), order_scope AS (
                    SELECT orders.platform,
                           COALESCE(alias.canonical_store, orders.store)
                               AS store,
                           orders.order_id,
                           orders.seller_sku
                    FROM effective_platform_order_lines AS orders
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=orders.platform
                     AND alias.source_store=orders.store
                    WHERE orders.resolution_status='ready'
                      AND substr(orders.ordered_at, 1, 10)=?
                      AND LOWER(TRIM(COALESCE(orders.order_status, '')))
                          NOT IN ('cancelled', 'canceled')
                    GROUP BY orders.platform, store, orders.order_id,
                             orders.seller_sku
                ), fulfillment_scope AS (
                    SELECT fulfillment.platform,
                           COALESCE(alias.canonical_store, fulfillment.store)
                               AS store,
                           fulfillment.order_id,
                           fulfillment.seller_sku,
                           fulfillment.mode
                    FROM order_fulfillment_snapshots AS fulfillment
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=fulfillment.platform
                     AND alias.source_store=fulfillment.store
                )
                SELECT metrics.platform, metrics.store,
                       SUM(CASE WHEN
                         (
                           metrics.sku_id<>'__ALL__'
                           AND (
                             EXISTS (
                               SELECT 1 FROM inventory_scope AS inventory
                               WHERE inventory.platform=metrics.platform
                                 AND inventory.store=metrics.store
                                 AND inventory.seller_sku=metrics.sku_id
                             )
                             OR (
                               EXISTS (
                                 SELECT 1 FROM order_scope AS orders
                                 WHERE orders.platform=metrics.platform
                                   AND orders.store=metrics.store
                                   AND orders.seller_sku=metrics.sku_id
                               )
                               AND NOT EXISTS (
                                 SELECT 1 FROM order_scope AS orders
                                 WHERE orders.platform=metrics.platform
                                   AND orders.store=metrics.store
                                   AND orders.seller_sku=metrics.sku_id
                                   AND NOT EXISTS (
                                     SELECT 1
                                     FROM fulfillment_scope AS fulfillment
                                     WHERE fulfillment.platform=orders.platform
                                       AND fulfillment.store=orders.store
                                       AND fulfillment.order_id=orders.order_id
                                       AND fulfillment.seller_sku=orders.seller_sku
                                       AND fulfillment.mode='supplier_fulfilled'
                                   )
                               )
                             )
                           )
                         )
                         OR (
                           metrics.sku_id='__ALL__'
                           AND EXISTS (
                             SELECT 1 FROM order_scope AS orders
                             WHERE orders.platform=metrics.platform
                               AND orders.store=metrics.store
                           )
                           AND NOT EXISTS (
                             SELECT 1 FROM order_scope AS orders
                             WHERE orders.platform=metrics.platform
                               AND orders.store=metrics.store
                               AND NOT EXISTS (
                                 SELECT 1 FROM inventory_scope AS inventory
                                 WHERE inventory.platform=orders.platform
                                   AND inventory.store=orders.store
                                   AND inventory.seller_sku=orders.seller_sku
                               )
                               AND NOT EXISTS (
                                 SELECT 1
                                 FROM fulfillment_scope AS fulfillment
                                 WHERE fulfillment.platform=orders.platform
                                   AND fulfillment.store=orders.store
                                   AND fulfillment.order_id=orders.order_id
                                   AND fulfillment.seller_sku=orders.seller_sku
                                   AND fulfillment.mode='supplier_fulfilled'
                               )
                           )
                         )
                         THEN 1 ELSE 0 END) AS item_count,
                       SUM(CASE WHEN
                         EXISTS (
                           SELECT 1 FROM order_scope AS orders
                           WHERE orders.platform=metrics.platform
                             AND orders.store=metrics.store
                             AND (
                               metrics.sku_id='__ALL__'
                               OR orders.seller_sku=metrics.sku_id
                             )
                         )
                         AND NOT EXISTS (
                           SELECT 1 FROM order_scope AS orders
                           WHERE orders.platform=metrics.platform
                             AND orders.store=metrics.store
                             AND (
                               metrics.sku_id='__ALL__'
                               OR orders.seller_sku=metrics.sku_id
                             )
                             AND NOT EXISTS (
                               SELECT 1
                               FROM fulfillment_scope AS fulfillment
                               WHERE fulfillment.platform=orders.platform
                                 AND fulfillment.store=orders.store
                                 AND fulfillment.order_id=orders.order_id
                                 AND fulfillment.seller_sku=orders.seller_sku
                                 AND fulfillment.mode='supplier_fulfilled'
                             )
                         )
                         THEN 1 ELSE 0 END) AS supplier_count
                FROM daily_ad_metrics AS metrics
                WHERE metrics.data_date=?
                GROUP BY metrics.platform, metrics.store
                """,
                (day, day, day),
            ).fetchall()
        inventory_ready_counts = {
            (row["platform"], row["store"]): int(row["item_count"] or 0)
            for row in inventory_strategy_rows
        }
        supplier_fulfilled_counts = {
            (row["platform"], row["store"]): int(
                row["supplier_count"] or 0
            )
            for row in inventory_strategy_rows
        }
        profit_counts = _keyed_counts(
            connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT profits.platform, profits.store,
                       COUNT(*) AS item_count
                FROM validated_profits AS profits
                JOIN daily_ad_metrics AS metrics
                  ON metrics.platform=profits.platform
                 AND metrics.store=profits.store
                 AND metrics.account_id=profits.account_id
                 AND metrics.campaign_id=profits.campaign_id
                 AND metrics.sku_id=profits.sku_id
                 AND metrics.data_date=profits.data_date
                WHERE metrics.data_date=?
                GROUP BY profits.platform, profits.store
                """,
                (day,),
            ).fetchall()
        )
        lineage_rows = connection.execute(
            """
            SELECT lineage.platform, lineage.store, lineage.input_name,
                   lineage.status, lineage.source_type,
                   lineage.source_date, lineage.age_days,
                   lineage.source_ref, lineage.detail_code,
                   COUNT(*) AS covered_rows
            FROM profit_input_lineage AS lineage
            JOIN daily_ad_metrics AS metrics
              ON metrics.platform=lineage.platform
             AND metrics.store=lineage.store
             AND metrics.account_id=lineage.account_id
             AND metrics.campaign_id=lineage.campaign_id
             AND metrics.sku_id=lineage.sku_id
             AND metrics.data_date=lineage.data_date
            WHERE metrics.data_date=?
            GROUP BY lineage.platform, lineage.store, lineage.input_name,
                     lineage.status, lineage.source_type,
                     lineage.source_date, lineage.age_days,
                     lineage.source_ref, lineage.detail_code
            ORDER BY lineage.platform, lineage.store, lineage.input_name,
                     lineage.status, lineage.source_date
            """,
            (day,),
        ).fetchall()
        lineage = tuple(
            ProfitInputLineageView(
                platform=row["platform"],
                store=row["store"],
                input_name=row["input_name"],
                status=row["status"],
                source_type=row["source_type"],
                source_date=(
                    None
                    if row["source_date"] is None
                    else date.fromisoformat(row["source_date"])
                ),
                age_days=row["age_days"],
                source_ref=row["source_ref"],
                detail_code=row["detail_code"],
                covered_rows=int(row["covered_rows"]),
                total_rows=metric_counts[(row["platform"], row["store"])],
            )
            for row in lineage_rows
        )
        lineage_by_store: dict[
            tuple[str, str], list[ProfitInputLineageView]
        ] = {}
        for item in lineage:
            lineage_by_store.setdefault((item.platform, item.store), []).append(
                item
            )
        fee_by_store = {
            (row["platform"], row["store"]): row for row in fee_rows
        }
        sources = []
        for row in metric_rows:
            key = (row["platform"], row["store"])
            total = int(row["metric_rows"])
            store_lineage = lineage_by_store.get(key, [])
            coverage = {
                name: sum(
                    item.covered_rows
                    for item in store_lineage
                    if item.input_name == name
                    and item.status in READY_LINEAGE_STATUSES
                )
                for name in PROFIT_INPUT_NAMES
            }
            missing = tuple(
                name for name in PROFIT_INPUT_NAMES if coverage[name] < total
            )
            profit_count = profit_counts.get(key, 0)
            profit_status = (
                "ready"
                if profit_count == total and not missing
                else "partial"
                if profit_count > 0
                else "pending_data"
            )
            exchange_items = [
                item
                for item in store_lineage
                if item.input_name == "exchange_rate"
                and item.status in READY_LINEAGE_STATUSES
            ]
            exchange_dates = {
                item.source_date
                for item in exchange_items
                if item.source_date is not None
            }
            exchange_details = {
                item.detail_code for item in exchange_items if item.detail_code
            }
            fee = fee_by_store.get(key)
            refund_observations, refund_amounts = refund_counts.get(key, (0, 0))
            sources.append(
                ProfitSourceSummary(
                    platform=row["platform"],
                    store=row["store"],
                    metric_rows=total,
                    attributed_orders=int(row["attributed_orders"] or 0),
                    platform_orders=order_counts.get(key, 0),
                    confirmed_cost_orders=cost_counts.get(key, 0),
                    refund_observations=refund_observations,
                    refund_amount_observations=refund_amounts,
                    inventory_snapshot_date=inventory_dates.get(key),
                    exchange_source_date=(
                        max(exchange_dates) if exchange_dates else None
                    ),
                    exchange_resolution_status=(
                        next(iter(exchange_details))
                        if len(exchange_details) == 1
                        else "mixed"
                        if exchange_details
                        else "pending_data"
                    ),
                    fee_period_start=(
                        None
                        if fee is None or fee["fee_period_start"] is None
                        else date.fromisoformat(fee["fee_period_start"])
                    ),
                    fee_period_end=(
                        None
                        if fee is None or fee["fee_period_end"] is None
                        else date.fromisoformat(fee["fee_period_end"])
                    ),
                    fee_selection=(
                        "pending_data" if fee is None else fee["fee_selection"]
                    ),
                    profit_status=profit_status,
                    missing_inputs=missing,
                )
            )

        metric_count = sum(metric_counts.values())
        product_cost_covered = sum(
            item.covered_rows
            for item in lineage
            if item.input_name == "product_cost"
            and item.status in READY_LINEAGE_STATUSES
        )
        profit_covered = sum(profit_counts.values())
        inventory_covered = sum(inventory_ready_counts.values())
        supplier_fulfilled = sum(supplier_fulfilled_counts.values())
        capabilities = {
            "platform_metrics": "ready" if metric_count else "pending_data",
            "estimated_profit": _coverage_status(
                product_cost_covered, metric_count
            ),
            "verified_profit": _coverage_status(profit_covered, metric_count),
            "inventory_safe_strategy": (
                "not_applicable"
                if supplier_fulfilled == metric_count
                and metric_count > 0
                else "ready"
                if profit_covered == metric_count
                and inventory_covered == metric_count
                and metric_count > 0
                else "partial"
                if profit_covered > 0 and inventory_covered > 0
                else "pending_data"
            ),
        }
        return tuple(sources), lineage, capabilities

    def dashboard(self, data_date: date) -> DashboardSnapshot:
        start = data_date - timedelta(days=29)
        with self.database.connect() as connection:
            trend_rows = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT m.data_date,
                       SUM(CAST(m.spend AS REAL)) spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) gmv,
                       SUM(CAST(p.net_profit_cny AS REAL)) net_profit,
                       COUNT(p.net_profit_cny) profit_count,
                       COUNT(m.id) metric_count
                FROM effective_metrics AS m
                LEFT JOIN validated_profits AS p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                WHERE m.data_date BETWEEN ? AND ?
                GROUP BY m.data_date
                ORDER BY m.data_date
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
                    net_profit=(
                        None
                        if row["profit_count"] != row["metric_count"]
                        else Decimal(str(row["net_profit"] or 0)).quantize(
                            Decimal("0.01")
                        )
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

    def weekly(self, week_end: date) -> dict:
        """返回周报聚合数据。

        查询指定周结束日期往前 7 天的数据，包含：
        - 广告指标聚合（按平台）
        - 利润聚合
        - 每日趋势
        - 异常分布
        - 建议分布
        - 采集质量
        - 环比对比（上周同期）
        """
        week_start = week_end - timedelta(days=6)
        prev_week_end = week_end - timedelta(days=7)
        prev_week_start = week_end - timedelta(days=13)
        start_str = week_start.isoformat()
        end_str = week_end.isoformat()
        prev_start_str = prev_week_start.isoformat()
        prev_end_str = prev_week_end.isoformat()

        with self.database.connect() as connection:
            platform_rows = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT m.platform,
                       SUM(CAST(m.spend AS REAL)) AS spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) AS gmv,
                       SUM(m.orders) AS orders,
                       SUM(CAST(p.net_sales_cny AS REAL))
                           AS attributed_sales_cny,
                       SUM(CAST(p.platform_commission_cny AS REAL))
                           AS platform_fee_cny,
                       SUM(CAST(m.spend AS REAL)
                           * CAST(r.rate_to_cny AS REAL)) AS ad_spend_cny,
                       SUM(CAST(p.net_profit_cny AS REAL)) AS net_profit,
                       SUM(CAST(p.gross_profit_cny AS REAL)) AS gross_profit,
                       COUNT(p.net_profit_cny) AS profit_count,
                       COUNT(r.rate_to_cny) AS rate_count,
                       COUNT(m.id) AS metric_count
                FROM effective_metrics m
                LEFT JOIN validated_profits p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                LEFT JOIN exchange_rates r
                  ON r.currency=m.currency AND r.rate_date=m.data_date
                WHERE m.data_date BETWEEN ? AND ?
                GROUP BY m.platform
                ORDER BY m.platform
                """,
                (start_str, end_str),
            ).fetchall()

            prev_row = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT SUM(CAST(m.spend AS REAL)) AS spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) AS gmv,
                       SUM(m.orders) AS orders,
                       SUM(CAST(p.net_profit_cny AS REAL)) AS net_profit,
                       COUNT(p.net_profit_cny) AS profit_count,
                       COUNT(m.id) AS metric_count
                FROM effective_metrics m
                LEFT JOIN validated_profits p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                WHERE m.data_date BETWEEN ? AND ?
                """,
                (prev_start_str, prev_end_str),
            ).fetchone()

            trend_rows = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT m.data_date,
                       SUM(CAST(m.spend AS REAL)) AS spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) AS gmv,
                       SUM(CAST(p.net_profit_cny AS REAL)) AS net_profit,
                       COUNT(p.net_profit_cny) AS profit_count,
                       COUNT(m.id) AS metric_count
                FROM effective_metrics m
                LEFT JOIN validated_profits p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                WHERE m.data_date BETWEEN ? AND ?
                GROUP BY m.data_date
                ORDER BY m.data_date
                """,
                (start_str, end_str),
            ).fetchall()

            alert_rows = connection.execute(
                """
                SELECT severity, COUNT(*) AS count
                FROM alerts
                WHERE data_date BETWEEN ? AND ?
                GROUP BY severity
                """,
                (start_str, end_str),
            ).fetchall()

            recommendation_rows = connection.execute(
                """
                SELECT action, status, COUNT(*) AS count
                FROM recommendations
                WHERE data_date BETWEEN ? AND ?
                GROUP BY action, status
                """,
                (start_str, end_str),
            ).fetchall()

            collection_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM collection_runs
                WHERE substr(started_at, 1, 10) BETWEEN ? AND ?
                GROUP BY status
                """,
                (start_str, end_str),
            ).fetchall()

        # 构建平台汇总
        platforms = []
        total_spend = Decimal(0)
        total_gmv = Decimal(0)
        total_orders = 0
        total_net_profit = Decimal(0)
        total_gross_profit = Decimal(0)
        total_sales_cny = Decimal(0)
        total_platform_fee = Decimal(0)
        total_ad_spend_cny = Decimal(0)
        all_breakdown_ready = True

        for row in platform_rows:
            spend = Decimal(str(row["spend"] or 0))
            gmv = Decimal(str(row["gmv"] or 0))
            breakdown_ready = (
                row["profit_count"] == row["metric_count"]
                and row["rate_count"] == row["metric_count"]
            )
            if not breakdown_ready:
                all_breakdown_ready = False
            sales_cny = (
                Decimal(str(row["attributed_sales_cny"])).quantize(
                    Decimal("0.01")
                )
                if breakdown_ready
                else None
            )
            platform_fee = (
                Decimal(str(row["platform_fee_cny"])).quantize(
                    Decimal("0.01")
                )
                if breakdown_ready
                else None
            )
            ad_spend_cny = (
                Decimal(str(row["ad_spend_cny"])).quantize(Decimal("0.01"))
                if breakdown_ready
                else None
            )
            net_profit = (
                Decimal(str(row["net_profit"])).quantize(Decimal("0.01"))
                if breakdown_ready
                else None
            )
            gross_profit = (
                Decimal(str(row["gross_profit"])).quantize(Decimal("0.01"))
                if breakdown_ready
                else None
            )
            sku_cost = (
                None
                if not breakdown_ready
                else (
                    sales_cny - platform_fee - ad_spend_cny - net_profit
                ).quantize(Decimal("0.01"))
            )
            platforms.append({
                "platform": row["platform"],
                "spend": spend.quantize(Decimal("0.01")),
                "gmv": gmv.quantize(Decimal("0.01")),
                "orders": int(row["orders"] or 0),
                "roas": (
                    None
                    if spend == 0
                    else (gmv / spend).quantize(Decimal("0.0001"))
                ),
                "net_profit": net_profit,
                "gross_profit": gross_profit,
                "attributed_sales_cny": sales_cny,
                "platform_fee_cny": platform_fee,
                "ad_spend_cny": ad_spend_cny,
                "sku_and_other_cost_cny": sku_cost,
            })
            total_spend += spend
            total_gmv += gmv
            total_orders += int(row["orders"] or 0)
            if net_profit is not None:
                total_net_profit += net_profit
            if gross_profit is not None:
                total_gross_profit += gross_profit
            if sales_cny is not None:
                total_sales_cny += sales_cny
            if platform_fee is not None:
                total_platform_fee += platform_fee
            if ad_spend_cny is not None:
                total_ad_spend_cny += ad_spend_cny

        total_sku_cost = (
            None
            if not all_breakdown_ready
            else (
                total_sales_cny
                - total_platform_fee
                - total_ad_spend_cny
                - total_net_profit
            ).quantize(Decimal("0.01"))
        )
        totals = {
            "spend": total_spend.quantize(Decimal("0.01")),
            "gmv": total_gmv.quantize(Decimal("0.01")),
            "orders": total_orders,
            "roas": (
                None
                if total_spend == 0
                else (total_gmv / total_spend).quantize(Decimal("0.0001"))
            ),
            "net_profit": (
                total_net_profit.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "gross_profit": (
                total_gross_profit.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "attributed_sales_cny": (
                total_sales_cny.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "platform_fee_cny": (
                total_platform_fee.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "ad_spend_cny": (
                total_ad_spend_cny.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "sku_and_other_cost_cny": total_sku_cost,
        }

        # 环比数据
        prev_spend = (
            Decimal(str(prev_row["spend"] or 0))
            if prev_row and prev_row["spend"]
            else Decimal(0)
        )
        prev_gmv = (
            Decimal(str(prev_row["gmv"] or 0))
            if prev_row and prev_row["gmv"]
            else Decimal(0)
        )
        prev_orders = (
            int(prev_row["orders"] or 0) if prev_row and prev_row["orders"] else 0
        )
        prev_net_profit = (
            Decimal(str(prev_row["net_profit"]))
            if prev_row
            and prev_row["net_profit"] is not None
            and prev_row["profit_count"] == prev_row["metric_count"]
            else None
        )
        mom = {
            "spend": _calc_mom(
                total_spend, prev_spend if prev_spend > 0 else None
            ),
            "gmv": _calc_mom(
                total_gmv, prev_gmv if prev_gmv > 0 else None
            ),
            "orders": _calc_mom(
                Decimal(str(total_orders)),
                Decimal(str(prev_orders)) if prev_orders > 0 else None,
            ),
            "net_profit": _calc_mom(
                total_net_profit if all_breakdown_ready else Decimal(0),
                prev_net_profit,
            ),
        }

        # 每日趋势
        daily_trends = tuple(
            TrendPoint(
                data_date=date.fromisoformat(row["data_date"]),
                spend=Decimal(str(row["spend"] or 0)),
                gmv=Decimal(str(row["gmv"] or 0)),
                roas=None,
                net_profit=(
                    None
                    if row["profit_count"] != row["metric_count"]
                    else Decimal(str(row["net_profit"] or 0)).quantize(
                        Decimal("0.01")
                    )
                ),
            )
            for row in trend_rows
        )

        # 异常分布
        alerts: dict[str, int] = {}
        for row in alert_rows:
            alerts[row["severity"]] = int(row["count"])

        # 建议分布
        recommendations: dict[str, int] = {}
        for row in recommendation_rows:
            key = (
                row["action"]
                if row["status"] != "approved"
                else "approved"
            )
            recommendations[key] = (
                recommendations.get(key, 0) + int(row["count"])
            )

        # 采集质量
        collection: dict[str, int] = {}
        for row in collection_rows:
            collection[row["status"]] = int(row["count"])
        success_days = collection.get("success", 0)
        total_days = sum(collection.values()) if collection else 7

        return {
            "period_start": week_start,
            "period_end": week_end,
            "platforms": tuple(platforms),
            "totals": totals,
            "mom": mom,
            "daily_trends": daily_trends,
            "alerts": alerts,
            "recommendations": recommendations,
            "collection": {
                "success_days": success_days,
                "failed_runs": total_days - success_days,
                "total_days": total_days,
            },
        }

    def monthly(self, year: int, month: int) -> dict:
        """返回月报聚合数据。

        查询指定年月的全月数据，包含：
        - 广告指标聚合（按平台）
        - 利润聚合（含经营利润拆解）
        - 费用聚合（按类别）
        - 每周趋势
        - 异常分布
        - 建议分布
        - 环比对比（上月同期）
        - 保本分析
        """
        month_start = date(year, month, 1)
        if month == 12:
            month_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(year, month + 1, 1) - timedelta(days=1)

        prev_month = month - 1
        prev_year = year
        if prev_month == 0:
            prev_month = 12
            prev_year = year - 1
        prev_month_start = date(prev_year, prev_month, 1)
        if prev_month == 12:
            prev_month_end = date(prev_year + 1, 1, 1) - timedelta(days=1)
        else:
            prev_month_end = date(prev_year, prev_month + 1, 1) - timedelta(
                days=1
            )

        start_str = month_start.isoformat()
        end_str = month_end.isoformat()
        prev_start_str = prev_month_start.isoformat()
        prev_end_str = prev_month_end.isoformat()

        with self.database.connect() as connection:
            platform_rows = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT m.platform,
                       SUM(CAST(m.spend AS REAL)) AS spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) AS gmv,
                       SUM(m.orders) AS orders,
                       SUM(CAST(p.net_sales_cny AS REAL))
                           AS attributed_sales_cny,
                       SUM(CAST(p.platform_commission_cny AS REAL))
                           AS platform_fee_cny,
                       SUM(CAST(m.spend AS REAL)
                           * CAST(r.rate_to_cny AS REAL)) AS ad_spend_cny,
                       SUM(CAST(p.net_profit_cny AS REAL)) AS net_profit,
                       SUM(CAST(p.gross_profit_cny AS REAL)) AS gross_profit,
                       COUNT(p.net_profit_cny) AS profit_count,
                       COUNT(r.rate_to_cny) AS rate_count,
                       COUNT(m.id) AS metric_count
                FROM effective_metrics m
                LEFT JOIN validated_profits p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                LEFT JOIN exchange_rates r
                  ON r.currency=m.currency AND r.rate_date=m.data_date
                WHERE m.data_date BETWEEN ? AND ?
                GROUP BY m.platform
                ORDER BY m.platform
                """,
                (start_str, end_str),
            ).fetchall()

            prev_row = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT SUM(CAST(m.spend AS REAL)) AS spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) AS gmv,
                       SUM(m.orders) AS orders,
                       SUM(CAST(p.net_profit_cny AS REAL)) AS net_profit,
                       COUNT(p.net_profit_cny) AS profit_count,
                       COUNT(m.id) AS metric_count
                FROM effective_metrics m
                LEFT JOIN validated_profits p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                WHERE m.data_date BETWEEN ? AND ?
                """,
                (prev_start_str, prev_end_str),
            ).fetchone()

            trend_rows = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT m.data_date,
                       SUM(CAST(m.spend AS REAL)) AS spend,
                       SUM(CAST(m.attributed_gmv AS REAL)) AS gmv,
                       SUM(CAST(p.net_profit_cny AS REAL)) AS net_profit,
                       COUNT(p.net_profit_cny) AS profit_count,
                       COUNT(m.id) AS metric_count
                FROM effective_metrics m
                LEFT JOIN validated_profits p
                  ON p.platform=m.platform AND p.store=m.store
                 AND p.account_id=m.account_id
                 AND p.campaign_id=m.campaign_id AND p.sku_id=m.sku_id
                 AND p.data_date=m.data_date
                WHERE m.data_date BETWEEN ? AND ?
                GROUP BY m.data_date
                ORDER BY m.data_date
                """,
                (start_str, end_str),
            ).fetchall()

            alert_rows = connection.execute(
                """
                SELECT severity, COUNT(*) AS count
                FROM alerts
                WHERE data_date BETWEEN ? AND ?
                GROUP BY severity
                """,
                (start_str, end_str),
            ).fetchall()

            recommendation_rows = connection.execute(
                """
                SELECT action, status, COUNT(*) AS count
                FROM recommendations
                WHERE data_date BETWEEN ? AND ?
                GROUP BY action, status
                """,
                (start_str, end_str),
            ).fetchall()

            expense_rows = connection.execute(
                """
                SELECT category, SUM(CAST(amount_cny AS REAL)) AS amount_cny
                FROM expense_entries
                WHERE occurred_on BETWEEN ? AND ?
                  AND status = 'confirmed'
                GROUP BY category
                ORDER BY category
                """,
                (start_str, end_str),
            ).fetchall()

            break_even_row = connection.execute(
                f"""
                WITH {_VALIDATED_PROFITS_CTE}
                SELECT AVG(CAST(p.break_even_roas AS REAL))
                       AS avg_break_even,
                       SUM(CASE
                           WHEN CAST(p.net_profit_cny AS REAL) >= 0
                           THEN 1 ELSE 0
                       END) AS healthy_skus,
                       SUM(CASE
                           WHEN CAST(p.net_profit_cny AS REAL) < 0
                           THEN 1 ELSE 0
                       END) AS losing_skus
                FROM validated_profits p
                JOIN daily_ad_metrics m
                  ON m.platform=p.platform AND m.store=p.store
                 AND m.account_id=p.account_id
                 AND m.campaign_id=p.campaign_id
                 AND m.sku_id=p.sku_id
                 AND m.data_date=p.data_date
                WHERE m.data_date BETWEEN ? AND ?
                """,
                (start_str, end_str),
            ).fetchone()

        # 构建平台汇总
        platforms = []
        total_spend = Decimal(0)
        total_gmv = Decimal(0)
        total_orders = 0
        total_net_profit = Decimal(0)
        total_gross_profit = Decimal(0)
        total_sales_cny = Decimal(0)
        total_platform_fee = Decimal(0)
        total_ad_spend_cny = Decimal(0)
        all_breakdown_ready = True

        for row in platform_rows:
            spend = Decimal(str(row["spend"] or 0))
            gmv = Decimal(str(row["gmv"] or 0))
            breakdown_ready = (
                row["profit_count"] == row["metric_count"]
                and row["rate_count"] == row["metric_count"]
            )
            if not breakdown_ready:
                all_breakdown_ready = False
            sales_cny = (
                Decimal(str(row["attributed_sales_cny"])).quantize(
                    Decimal("0.01")
                )
                if breakdown_ready
                else None
            )
            platform_fee = (
                Decimal(str(row["platform_fee_cny"])).quantize(
                    Decimal("0.01")
                )
                if breakdown_ready
                else None
            )
            ad_spend_cny = (
                Decimal(str(row["ad_spend_cny"])).quantize(Decimal("0.01"))
                if breakdown_ready
                else None
            )
            net_profit = (
                Decimal(str(row["net_profit"])).quantize(Decimal("0.01"))
                if breakdown_ready
                else None
            )
            gross_profit = (
                Decimal(str(row["gross_profit"])).quantize(Decimal("0.01"))
                if breakdown_ready
                else None
            )
            sku_cost = (
                None
                if not breakdown_ready
                else (
                    sales_cny - platform_fee - ad_spend_cny - net_profit
                ).quantize(Decimal("0.01"))
            )
            platforms.append({
                "platform": row["platform"],
                "spend": spend.quantize(Decimal("0.01")),
                "gmv": gmv.quantize(Decimal("0.01")),
                "orders": int(row["orders"] or 0),
                "roas": (
                    None
                    if spend == 0
                    else (gmv / spend).quantize(Decimal("0.0001"))
                ),
                "net_profit": net_profit,
                "gross_profit": gross_profit,
                "attributed_sales_cny": sales_cny,
                "platform_fee_cny": platform_fee,
                "ad_spend_cny": ad_spend_cny,
                "sku_and_other_cost_cny": sku_cost,
            })
            total_spend += spend
            total_gmv += gmv
            total_orders += int(row["orders"] or 0)
            if net_profit is not None:
                total_net_profit += net_profit
            if gross_profit is not None:
                total_gross_profit += gross_profit
            if sales_cny is not None:
                total_sales_cny += sales_cny
            if platform_fee is not None:
                total_platform_fee += platform_fee
            if ad_spend_cny is not None:
                total_ad_spend_cny += ad_spend_cny

        total_sku_cost = (
            None
            if not all_breakdown_ready
            else (
                total_sales_cny
                - total_platform_fee
                - total_ad_spend_cny
                - total_net_profit
            ).quantize(Decimal("0.01"))
        )
        totals = {
            "spend": total_spend.quantize(Decimal("0.01")),
            "gmv": total_gmv.quantize(Decimal("0.01")),
            "orders": total_orders,
            "roas": (
                None
                if total_spend == 0
                else (total_gmv / total_spend).quantize(Decimal("0.0001"))
            ),
            "net_profit": (
                total_net_profit.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "gross_profit": (
                total_gross_profit.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "attributed_sales_cny": (
                total_sales_cny.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "platform_fee_cny": (
                total_platform_fee.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "ad_spend_cny": (
                total_ad_spend_cny.quantize(Decimal("0.01"))
                if all_breakdown_ready
                else None
            ),
            "sku_and_other_cost_cny": total_sku_cost,
        }

        # 环比
        prev_spend = (
            Decimal(str(prev_row["spend"] or 0))
            if prev_row and prev_row["spend"]
            else Decimal(0)
        )
        prev_gmv = (
            Decimal(str(prev_row["gmv"] or 0))
            if prev_row and prev_row["gmv"]
            else Decimal(0)
        )
        prev_orders = (
            int(prev_row["orders"] or 0) if prev_row and prev_row["orders"] else 0
        )
        prev_net_profit = (
            Decimal(str(prev_row["net_profit"]))
            if prev_row
            and prev_row["net_profit"] is not None
            and prev_row["profit_count"] == prev_row["metric_count"]
            else None
        )
        mom = {
            "spend": _calc_mom(
                total_spend, prev_spend if prev_spend > 0 else None
            ),
            "gmv": _calc_mom(
                total_gmv, prev_gmv if prev_gmv > 0 else None
            ),
            "orders": _calc_mom(
                Decimal(str(total_orders)),
                Decimal(str(prev_orders)) if prev_orders > 0 else None,
            ),
            "net_profit": _calc_mom(
                total_net_profit if all_breakdown_ready else Decimal(0),
                prev_net_profit,
            ),
        }

        # 每周趋势（按 ISO 周分组）
        weekly_buckets: dict[tuple[int, int], list] = {}
        for row in trend_rows:
            d = date.fromisoformat(row["data_date"])
            iso_year, iso_week, _ = d.isocalendar()
            key = (iso_year, iso_week)
            weekly_buckets.setdefault(key, []).append(row)

        weekly_trends = []
        for key in sorted(weekly_buckets):
            rows = weekly_buckets[key]
            week_spend = sum(Decimal(str(r["spend"] or 0)) for r in rows)
            week_gmv = sum(Decimal(str(r["gmv"] or 0)) for r in rows)
            profit_values = [
                Decimal(str(r["net_profit"]))
                for r in rows
                if r["profit_count"] == r["metric_count"]
                and r["net_profit"] is not None
            ]
            week_net_profit = (
                sum(profit_values).quantize(Decimal("0.01"))
                if len(profit_values) == len(rows)
                else None
            )
            weekly_trends.append(
                TrendPoint(
                    data_date=date.fromisocalendar(*key, 1),
                    spend=week_spend.quantize(Decimal("0.01")),
                    gmv=week_gmv.quantize(Decimal("0.01")),
                    roas=None,
                    net_profit=week_net_profit,
                )
            )

        # 异常分布
        alerts: dict[str, int] = {}
        for row in alert_rows:
            alerts[row["severity"]] = int(row["count"])

        # 建议分布
        recommendations: dict[str, int] = {}
        for row in recommendation_rows:
            rkey = (
                row["action"]
                if row["status"] != "approved"
                else "approved"
            )
            recommendations[rkey] = (
                recommendations.get(rkey, 0) + int(row["count"])
            )

        # 费用
        expenses = tuple(
            {
                "category": row["category"],
                "amount_cny": Decimal(str(row["amount_cny"])).quantize(
                    Decimal("0.01")
                ),
            }
            for row in expense_rows
        )

        # 保本分析
        avg_break_even = (
            Decimal(str(break_even_row["avg_break_even"])).quantize(
                Decimal("0.00")
            )
            if break_even_row
            and break_even_row["avg_break_even"] is not None
            else None
        )
        healthy_skus = (
            int(break_even_row["healthy_skus"])
            if break_even_row
            and break_even_row["healthy_skus"] is not None
            else 0
        )
        losing_skus = (
            int(break_even_row["losing_skus"])
            if break_even_row
            and break_even_row["losing_skus"] is not None
            else 0
        )

        return {
            "year": year,
            "month": month,
            "period_start": month_start,
            "period_end": month_end,
            "platforms": tuple(platforms),
            "totals": totals,
            "mom": mom,
            "weekly_trends": tuple(weekly_trends),
            "alerts": alerts,
            "recommendations": recommendations,
            "expenses": expenses,
            "break_even": {
                "avg_break_even_roas": avg_break_even,
                "healthy_skus": healthy_skus,
                "losing_skus": losing_skus,
            },
        }


def _calc_mom(current: Decimal, previous: Decimal | None) -> str | None:
    """计算环比变化百分比，返回格式化的字符串。

    返回格式如 "↑ 15.2%"（增长）、"↓ 3.1%"（下降）、"→ 0.0%"（持平）。
    当上一期数据为空或为零时返回 None。
    """
    if previous is None or previous == 0:
        return None
    change_pct = float((current - previous) / previous * 100)
    direction = "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"
    return f"{direction} {abs(change_pct):.1f}%"


def _keyed_counts(rows) -> dict[tuple[str, str], int]:
    return {
        (row["platform"], row["store"]): int(row["item_count"])
        for row in rows
    }


def _coverage_status(covered: int, total: int) -> str:
    if total > 0 and covered >= total:
        return "ready"
    if covered > 0:
        return "partial"
    return "pending_data"

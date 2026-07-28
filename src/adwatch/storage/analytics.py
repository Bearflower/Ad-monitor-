from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

from adwatch.storage.db import Database


class AnalyticsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def seed_mock_business_data(self, data_date: date) -> int:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT platform, store, campaign_id, sku_id, currency
                FROM daily_ad_metrics
                WHERE data_date = ?
                """,
                (data_date.isoformat(),),
            ).fetchall()
            for row in rows:
                country = "MY" if row["platform"] == "tiktok" else "TH"
                connection.execute(
                    """
                    INSERT INTO stores(platform, store, country, currency)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(platform, store) DO UPDATE SET
                        country=excluded.country, currency=excluded.currency
                    """,
                    (row["platform"], row["store"], country, row["currency"]),
                )
                connection.execute(
                    """
                    INSERT INTO campaign_settings(
                        platform, campaign_id, start_date, target_roas,
                        current_budget, baseline_budget
                    ) VALUES (?, ?, '2026-07-01', '2.0', '100', '100')
                    ON CONFLICT(platform, campaign_id) DO UPDATE SET
                        target_roas=excluded.target_roas
                    """,
                    (row["platform"], row["campaign_id"]),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO sku_mappings(sku_id)
                    VALUES (?)
                    """,
                    (row["sku_id"],),
                )
                connection.execute(
                    """
                    INSERT INTO product_costs(
                        sku_id, effective_date, product_cost, commission_rate,
                        seller_shipping, coupons, allocated_fixed_cost,
                        refund_amount
                    ) VALUES (?, ?, '20', '0.08', '2', '1', '1', '0')
                    ON CONFLICT(sku_id, effective_date) DO NOTHING
                    """,
                    (row["sku_id"], data_date.isoformat()),
                )
                connection.execute(
                    """
                    INSERT INTO inventory_snapshots(
                        sku_id, snapshot_date, units, expected_daily_units
                    ) VALUES (?, ?, 100, '5')
                    ON CONFLICT(sku_id, snapshot_date) DO UPDATE SET
                        units=excluded.units,
                        expected_daily_units=excluded.expected_daily_units
                    """,
                    (row["sku_id"], data_date.isoformat()),
                )
                rate = {
                    "MYR": "1.55",
                    "THB": "0.21",
                    "CNY": "1",
                }.get(row["currency"], "1")
                connection.execute(
                    """
                    INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
                    VALUES (?, ?, ?)
                    ON CONFLICT(currency, rate_date) DO UPDATE SET
                        rate_to_cny=excluded.rate_to_cny
                    """,
                    (row["currency"], data_date.isoformat(), rate),
                )
        return len(rows)

    def load_analysis_rows(self, data_date: date) -> list[sqlite3.Row]:
        with self.database.connect() as connection:
            return connection.execute(
                """
                WITH metric_counts AS (
                    SELECT
                        platform, store, data_date,
                        COUNT(*) AS metric_count
                    FROM daily_ad_metrics
                    GROUP BY platform, store, data_date
                ),
                legacy_order_costs AS (
                    SELECT
                        line.platform,
                        COALESCE(alias.canonical_store, line.store)
                            AS canonical_store,
                        line.order_date,
                        SUM(CAST(line.line_cost_cny AS NUMERIC))
                            AS product_cost_cny,
                        1 AS inventory_required
                    FROM order_cost_lines AS line
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=line.platform
                     AND alias.source_store=line.store
                    GROUP BY
                        line.platform, canonical_store, line.order_date
                ),
                order_dates AS (
                    SELECT platform, store, order_id, seller_sku,
                           MIN(substr(ordered_at, 1, 10)) AS order_date
                    FROM platform_order_lines
                    GROUP BY platform, store, order_id, seller_sku
                ),
                snapshot_order_costs AS (
                    SELECT
                        snapshot.platform,
                        COALESCE(alias.canonical_store, snapshot.store)
                            AS canonical_store,
                        COALESCE(
                            movement.occurred_on,
                            order_dates.order_date
                        ) AS order_date,
                        SUM(CAST(snapshot.total_cost_cny AS NUMERIC))
                            AS product_cost_cny,
                        MAX(
                            CASE WHEN COALESCE(
                                fulfillment.mode, 'stocked'
                            )='stocked' THEN 1 ELSE 0 END
                        ) AS inventory_required
                    FROM order_cost_snapshots AS snapshot
                    LEFT JOIN order_fulfillment_snapshots AS fulfillment
                      ON fulfillment.platform=snapshot.platform
                     AND fulfillment.store=snapshot.store
                     AND fulfillment.order_id=snapshot.order_id
                     AND fulfillment.seller_sku=snapshot.seller_sku
                    LEFT JOIN inventory_movements AS movement
                      ON movement.source_type='order'
                     AND movement.movement_type='sale_out'
                     AND movement.source_id=(
                         snapshot.platform || ':' || snapshot.store || ':'
                         || snapshot.order_id
                     )
                     AND movement.seller_sku=snapshot.seller_sku
                    LEFT JOIN order_dates
                      ON order_dates.platform=snapshot.platform
                     AND order_dates.store=snapshot.store
                     AND order_dates.order_id=snapshot.order_id
                     AND order_dates.seller_sku=snapshot.seller_sku
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=snapshot.platform
                     AND alias.source_store=snapshot.store
                    WHERE snapshot.status='confirmed'
                      AND (
                          movement.occurred_on IS NOT NULL
                          OR fulfillment.mode='supplier_fulfilled'
                      )
                    GROUP BY
                        snapshot.platform, canonical_store,
                        order_date
                ),
                daily_order_costs AS (
                    SELECT * FROM legacy_order_costs
                    UNION ALL
                    SELECT snapshot.*
                    FROM snapshot_order_costs AS snapshot
                    WHERE NOT EXISTS (
                        SELECT 1 FROM legacy_order_costs AS legacy
                        WHERE legacy.platform=snapshot.platform
                          AND legacy.canonical_store=
                              snapshot.canonical_store
                          AND legacy.order_date=snapshot.order_date
                    )
                )
                SELECT
                    metric.*,
                    cost.product_cost,
                    cost.commission_rate,
                    cost.seller_shipping,
                    cost.coupons,
                    cost.allocated_fixed_cost,
                    cost.refund_amount,
                    inventory.units AS inventory_units,
                    inventory.expected_daily_units,
                    rate.rate_to_cny,
                    campaign.start_date,
                    campaign.target_roas,
                    campaign.current_budget,
                    campaign.baseline_budget,
                    retest.available_test_budget,
                    COALESCE(retest.enabled, 0) AS retest_candidate,
                    CASE WHEN metric_counts.metric_count = 1
                         THEN CAST(
                             daily_order_costs.product_cost_cny AS TEXT
                         )
                    END AS order_product_cost_cny,
                    CASE
                        WHEN daily_order_costs.product_cost_cny IS NOT NULL
                         AND metric_counts.metric_count > 1
                        THEN 1 ELSE 0
                    END AS order_cost_allocation_ambiguous
                    ,
                    CASE
                        WHEN daily_order_costs.product_cost_cny IS NOT NULL
                        THEN daily_order_costs.inventory_required
                        ELSE 1
                    END AS inventory_required
                FROM daily_ad_metrics AS metric
                LEFT JOIN product_costs AS cost
                    ON cost.sku_id = metric.sku_id
                    AND cost.effective_date = (
                        SELECT MAX(c2.effective_date)
                        FROM product_costs AS c2
                        WHERE c2.sku_id = metric.sku_id
                          AND c2.effective_date <= metric.data_date
                    )
                LEFT JOIN inventory_snapshots AS inventory
                    ON inventory.sku_id = metric.sku_id
                    AND inventory.snapshot_date = metric.data_date
                LEFT JOIN exchange_rates AS rate
                    ON rate.currency = metric.currency
                    AND rate.rate_date = metric.data_date
                LEFT JOIN campaign_settings AS campaign
                    ON campaign.platform = metric.platform
                    AND campaign.campaign_id = metric.campaign_id
                LEFT JOIN product_retest_candidates AS retest
                    ON retest.platform = metric.platform
                    AND retest.campaign_id = metric.campaign_id
                    AND retest.sku_id = metric.sku_id
                LEFT JOIN metric_counts
                    ON metric_counts.platform=metric.platform
                    AND metric_counts.store=metric.store
                    AND metric_counts.data_date=metric.data_date
                LEFT JOIN daily_order_costs
                    ON daily_order_costs.platform=metric.platform
                    AND daily_order_costs.canonical_store=metric.store
                    AND daily_order_costs.order_date=metric.data_date
                WHERE metric.data_date = ?
                ORDER BY metric.platform, metric.campaign_id, metric.sku_id
                """,
                (data_date.isoformat(),),
            ).fetchall()

    def baseline_for(self, row: sqlite3.Row, data_date: date) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT AVG(CAST(spend AS REAL)) AS spend,
                       CASE WHEN SUM(CAST(spend AS REAL)) = 0 THEN NULL
                            ELSE SUM(CAST(attributed_gmv AS REAL))
                                 / SUM(CAST(spend AS REAL))
                       END AS roas
                FROM daily_ad_metrics
                WHERE platform=? AND store=? AND account_id=?
                  AND campaign_id=? AND sku_id=?
                  AND data_date BETWEEN date(?, '-7 days') AND date(?, '-1 day')
                """,
                (
                    row["platform"],
                    row["store"],
                    row["account_id"],
                    row["campaign_id"],
                    row["sku_id"],
                    data_date.isoformat(),
                    data_date.isoformat(),
                ),
            ).fetchone()

    def recent_webdriver_failures(self, limit: int = 3) -> int:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT status FROM collection_runs
                WHERE mode='ziniao'
                ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return sum(1 for row in rows if row["status"] == "failed")

    def consecutive_global_low_roas_days(
        self, data_date: date, limit: int = 2
    ) -> int:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT metric.data_date,
                       COUNT(*) AS total_count,
                       SUM(
                           CASE WHEN CAST(metric.roas AS REAL)
                                      < CAST(campaign.target_roas AS REAL) * 0.60
                                THEN 1 ELSE 0 END
                       ) AS low_count
                FROM daily_ad_metrics AS metric
                JOIN campaign_settings AS campaign
                  ON campaign.platform=metric.platform
                 AND campaign.campaign_id=metric.campaign_id
                WHERE metric.data_date <= ?
                GROUP BY metric.data_date
                ORDER BY metric.data_date DESC
                LIMIT ?
                """,
                (data_date.isoformat(), limit),
            ).fetchall()
        count = 0
        expected = data_date
        for row in rows:
            if date.fromisoformat(row["data_date"]) != expected:
                break
            if row["total_count"] == 0 or row["low_count"] != row["total_count"]:
                break
            count += 1
            expected = date.fromordinal(expected.toordinal() - 1)
        return count

    def consecutive_campaign_low_days(
        self, row: sqlite3.Row, data_date: date
    ) -> int:
        target = row["target_roas"]
        if target is None:
            return 0
        with self.database.connect() as connection:
            history = connection.execute(
                """
                SELECT data_date, roas
                FROM daily_ad_metrics
                WHERE platform=? AND store=? AND account_id=?
                  AND campaign_id=? AND sku_id=? AND data_date<=?
                ORDER BY data_date DESC
                LIMIT 30
                """,
                (
                    row["platform"],
                    row["store"],
                    row["account_id"],
                    row["campaign_id"],
                    row["sku_id"],
                    data_date.isoformat(),
                ),
            ).fetchall()
        count = 0
        expected = data_date
        threshold = Decimal(target) * Decimal("0.50")
        for point in history:
            if date.fromisoformat(point["data_date"]) != expected:
                break
            if point["roas"] is None or Decimal(point["roas"]) >= threshold:
                break
            count += 1
            expected = date.fromordinal(expected.toordinal() - 1)
        return count

    @staticmethod
    def decimal(row: sqlite3.Row, key: str) -> Decimal | None:
        value = row[key]
        return None if value is None else Decimal(value)

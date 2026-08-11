from __future__ import annotations

import sqlite3
from contextlib import nullcontext
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
                        refund_amount, refund_data_status, input_source_name
                    ) VALUES (
                        ?, ?, '20', '0.08', '2', '1', '1', '0',
                        'observed', 'mock'
                    )
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

    def load_analysis_rows(
        self, data_date: date, *, connection=None
    ) -> list[sqlite3.Row]:
        db_context = (
            nullcontext(connection)
            if connection is not None
            else self.database.connect()
        )
        with db_context as db_connection:
            return db_connection.execute(
                """
                WITH metric_counts AS (
                    SELECT
                        platform, store, data_date,
                        COUNT(*) AS metric_count,
                        COALESCE(
                            SUM(CAST(attributed_gmv AS NUMERIC)), 0
                        ) AS attributed_gmv_total
                    FROM daily_ad_metrics
                    GROUP BY platform, store, data_date
                ),
                metric_sku_scopes AS (
                    SELECT sku_id,
                           COUNT(DISTINCT platform || char(31) || store)
                               AS scope_count
                    FROM daily_ad_metrics
                    GROUP BY sku_id
                ),
                order_line_scope AS (
                    SELECT
                        line.platform,
                        COALESCE(alias.canonical_store, line.store)
                            AS canonical_store,
                        line.order_id,
                        line.seller_sku,
                        SUM(line.quantity) AS expected_quantity,
                        MIN(substr(line.ordered_at, 1, 10)) AS order_date
                    FROM effective_platform_order_lines AS line
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=line.platform
                     AND alias.source_store=line.store
                    WHERE line.resolution_status='ready'
                      AND LOWER(TRIM(COALESCE(line.order_status, '')))
                          NOT IN ('cancelled', 'canceled')
                      AND LOWER(TRIM(COALESCE(line.refund_status, '')))
                          NOT IN (
                              'returned', 'refund_completed', 'refunded',
                              'return_completed'
                          )
                    GROUP BY
                        line.platform,
                        COALESCE(alias.canonical_store, line.store),
                        line.order_id, line.seller_sku
                ),
                expected_order_costs AS (
                    SELECT platform, canonical_store, order_date,
                           COUNT(*) AS expected_line_count
                    FROM order_line_scope
                    GROUP BY platform, canonical_store, order_date
                ),
                legacy_line_scope AS (
                    SELECT
                        line.platform,
                        COALESCE(alias.canonical_store, line.store)
                            AS canonical_store,
                        line.order_id,
                        line.sku_id AS seller_sku,
                        line.order_date,
                        line.line_cost_cny
                    FROM order_cost_lines AS line
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=line.platform
                     AND alias.source_store=line.store
                ),
                legacy_order_costs_raw AS (
                    SELECT
                        line.platform,
                        line.canonical_store,
                        line.order_date,
                        SUM(CAST(line.line_cost_cny AS NUMERIC))
                            AS product_cost_cny,
                        1 AS inventory_required,
                        line.order_date AS cost_source_date,
                        'order_cost_lines' AS cost_source_type,
                        COUNT(*) AS cost_line_count
                    FROM legacy_line_scope AS line
                    GROUP BY
                        line.platform, line.canonical_store, line.order_date
                ),
                legacy_order_costs AS (
                    SELECT legacy.platform, legacy.canonical_store,
                           legacy.order_date, legacy.product_cost_cny,
                           legacy.inventory_required,
                           legacy.cost_source_date,
                           legacy.cost_source_type
                    FROM legacy_order_costs_raw AS legacy
                    LEFT JOIN expected_order_costs AS expected
                      ON expected.platform=legacy.platform
                     AND expected.canonical_store=legacy.canonical_store
                     AND expected.order_date=legacy.order_date
                    WHERE expected.expected_line_count IS NULL
                       OR (
                           NOT EXISTS (
                               SELECT 1
                               FROM order_line_scope AS expected_line
                               WHERE expected_line.platform=legacy.platform
                                 AND expected_line.canonical_store=
                                     legacy.canonical_store
                                 AND expected_line.order_date=
                                     legacy.order_date
                                 AND NOT EXISTS (
                                     SELECT 1
                                     FROM legacy_line_scope AS actual_line
                                     WHERE actual_line.platform=
                                           expected_line.platform
                                       AND actual_line.canonical_store=
                                           expected_line.canonical_store
                                       AND actual_line.order_date=
                                           expected_line.order_date
                                       AND actual_line.order_id=
                                           expected_line.order_id
                                       AND actual_line.seller_sku=
                                           expected_line.seller_sku
                                 )
                           )
                           AND NOT EXISTS (
                               SELECT 1
                               FROM legacy_line_scope AS actual_line
                               WHERE actual_line.platform=legacy.platform
                                 AND actual_line.canonical_store=
                                     legacy.canonical_store
                                 AND actual_line.order_date=legacy.order_date
                                 AND NOT EXISTS (
                                     SELECT 1
                                     FROM order_line_scope AS expected_line
                                     WHERE expected_line.platform=
                                           actual_line.platform
                                       AND expected_line.canonical_store=
                                           actual_line.canonical_store
                                       AND expected_line.order_date=
                                           actual_line.order_date
                                       AND expected_line.order_id=
                                           actual_line.order_id
                                       AND expected_line.seller_sku=
                                           actual_line.seller_sku
                                 )
                           )
                       )
                ),
                snapshot_line_costs AS (
                    SELECT
                        snapshot.platform,
                        COALESCE(snapshot_alias.canonical_store, snapshot.store)
                            AS canonical_store,
                        snapshot.order_id,
                        snapshot.seller_sku,
                        COALESCE(
                            movement.occurred_on,
                            order_scope.order_date
                        ) AS order_date,
                        CAST(snapshot.total_cost_cny AS NUMERIC)
                            AS product_cost_cny,
                        snapshot.quantity AS cost_quantity,
                        CASE WHEN COALESCE(
                            fulfillment.mode, 'stocked'
                        )='stocked' THEN 1 ELSE 0 END AS inventory_required,
                        snapshot.cost_effective_date AS cost_source_date,
                        ROW_NUMBER() OVER (
                            PARTITION BY
                                snapshot.platform,
                                COALESCE(
                                    snapshot_alias.canonical_store,
                                    snapshot.store
                                ),
                                snapshot.order_id,
                                snapshot.seller_sku
                            ORDER BY snapshot.created_at DESC, snapshot.store
                        ) AS snapshot_rank
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
                    LEFT JOIN store_aliases AS snapshot_alias
                      ON snapshot_alias.platform=snapshot.platform
                     AND snapshot_alias.source_store=snapshot.store
                    LEFT JOIN order_line_scope AS order_scope
                      ON order_scope.platform=snapshot.platform
                     AND order_scope.canonical_store=COALESCE(
                         snapshot_alias.canonical_store, snapshot.store
                     )
                     AND order_scope.order_id=snapshot.order_id
                     AND order_scope.seller_sku=snapshot.seller_sku
                    WHERE snapshot.status='confirmed'
                      AND (
                          movement.occurred_on IS NOT NULL
                          OR fulfillment.mode='supplier_fulfilled'
                      )
                ),
                snapshot_order_costs_raw AS (
                    SELECT
                        snapshot.platform,
                        snapshot.canonical_store,
                        snapshot.order_date,
                        SUM(snapshot.product_cost_cny) AS product_cost_cny,
                        MAX(snapshot.inventory_required) AS inventory_required,
                        MAX(snapshot.cost_source_date) AS cost_source_date,
                        'order_cost_snapshots' AS cost_source_type
                    FROM snapshot_line_costs AS snapshot
                    WHERE snapshot.snapshot_rank=1
                    GROUP BY
                        snapshot.platform,
                        snapshot.canonical_store,
                        snapshot.order_date
                ),
                snapshot_order_costs AS (
                    SELECT snapshot.platform, snapshot.canonical_store,
                           snapshot.order_date, snapshot.product_cost_cny,
                           snapshot.inventory_required,
                           snapshot.cost_source_date,
                           snapshot.cost_source_type
                    FROM snapshot_order_costs_raw AS snapshot
                    LEFT JOIN expected_order_costs AS expected
                      ON expected.platform=snapshot.platform
                     AND expected.canonical_store=snapshot.canonical_store
                     AND expected.order_date=snapshot.order_date
                    WHERE expected.expected_line_count IS NULL
                       OR NOT EXISTS (
                           SELECT 1
                           FROM order_line_scope AS expected_line
                           WHERE expected_line.platform=snapshot.platform
                             AND expected_line.canonical_store=
                                 snapshot.canonical_store
                             AND expected_line.order_date=snapshot.order_date
                             AND NOT EXISTS (
                                 SELECT 1
                                 FROM snapshot_line_costs AS actual_line
                                 WHERE actual_line.snapshot_rank=1
                                   AND actual_line.platform=
                                       expected_line.platform
                                   AND actual_line.canonical_store=
                                       expected_line.canonical_store
                                   AND actual_line.order_id=
                                       expected_line.order_id
                                   AND actual_line.seller_sku=
                                       expected_line.seller_sku
                                   AND actual_line.cost_quantity>=
                                       expected_line.expected_quantity
                             )
                       )
                ),
                daily_order_costs AS (
                    SELECT * FROM snapshot_order_costs
                    UNION ALL
                    SELECT legacy.*
                    FROM legacy_order_costs AS legacy
                    WHERE NOT EXISTS (
                        SELECT 1 FROM snapshot_order_costs AS snapshot
                        WHERE snapshot.platform=legacy.platform
                          AND snapshot.canonical_store=
                              legacy.canonical_store
                          AND snapshot.order_date=legacy.order_date
                    )
                )
                SELECT
                    metric.*,
                    cost.product_cost,
                    cost.effective_date AS cost_effective_date,
                    CASE
                        WHEN refund_input.status IS NOT NULL
                        THEN refund_input.status
                        ELSE cost.refund_data_status
                    END AS refund_data_status,
                    cost.input_source_name,
                    refund_input.source_type AS refund_source_type,
                    refund_input.source_ref AS refund_source_ref,
                    refund_input.data_date AS refund_source_date,
                    cost.commission_rate AS legacy_commission_rate,
                    CASE
                        WHEN fee.effective_rate IS NOT NULL
                        THEN fee.effective_rate
                        WHEN metric_sku_scopes.scope_count = 1
                        THEN cost.commission_rate
                    END AS resolved_commission_rate,
                    CASE
                        WHEN fee.effective_rate IS NOT NULL
                        THEN fee.effective_rate
                        WHEN metric_sku_scopes.scope_count = 1
                        THEN cost.commission_rate
                    END AS commission_rate,
                    fee.period_start AS fee_period_start,
                    fee.period_end AS fee_period_end,
                    CASE
                        WHEN fee.id IS NOT NULL
                         AND fee.period_end >= metric.data_date
                        THEN 'covering_period'
                        WHEN fee.id IS NOT NULL
                        THEN 'carried_forward'
                        WHEN cost.commission_rate IS NOT NULL
                         AND metric_sku_scopes.scope_count = 1
                        THEN 'legacy_cost_rate'
                    END AS fee_selection,
                    fee.source_name AS fee_source_name,
                    fee.source_sha256 AS fee_source_sha256,
                    cost.seller_shipping,
                    cost.coupons,
                    cost.allocated_fixed_cost,
                    CASE
                        WHEN refund_input.status IN ('observed', 't3_closed_zero')
                        THEN CASE
                             WHEN CAST(refund_input.refund_amount_original AS NUMERIC) = 0
                             THEN 0
                             ELSE CAST(refund_input.refund_amount_original AS NUMERIC)
                             * CASE
                                 WHEN metric_counts.attributed_gmv_total > 0
                                 THEN CAST(metric.attributed_gmv AS REAL)
                                      / CAST(metric_counts.attributed_gmv_total AS REAL)
                                 WHEN metric_counts.metric_count > 0
                                 THEN 1.0 / metric_counts.metric_count
                                 ELSE 0
                               END
                           END
                        WHEN refund_input.status IS NULL
                         AND cost.refund_data_status='observed'
                        THEN cost.refund_amount
                    END AS refund_amount,
                    inventory.units AS inventory_units,
                    inventory.expected_daily_units,
                    rate.rate_to_cny,
                    rate.source_date AS exchange_source_date,
                    rate.resolution_status AS exchange_resolution_status,
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
                    daily_order_costs.cost_source_date,
                    daily_order_costs.cost_source_type,
                    CASE
                        WHEN daily_order_costs.product_cost_cny IS NOT NULL
                         AND metric_counts.metric_count > 1
                        THEN 1 ELSE 0
                    END AS order_cost_allocation_ambiguous
                    ,
                    CASE
                        WHEN expected_order_costs.expected_line_count IS NOT NULL
                         AND daily_order_costs.product_cost_cny IS NULL
                        THEN 1 ELSE 0
                    END AS order_cost_coverage_incomplete,
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
                LEFT JOIN daily_refund_inputs AS refund_input
                    ON refund_input.platform = metric.platform
                    AND refund_input.store = metric.store
                    AND refund_input.data_date = metric.data_date
                LEFT JOIN platform_fee_statements AS fee
                    ON fee.id = (
                        SELECT candidate.id
                        FROM platform_fee_statements AS candidate
                        WHERE candidate.platform = metric.platform
                          AND candidate.store = metric.store
                          AND candidate.is_current = 1
                          AND candidate.period_start <= ?
                        ORDER BY
                            CASE
                                WHEN candidate.period_end >= ?
                                THEN 0 ELSE 1
                            END,
                            candidate.period_start DESC,
                            candidate.period_end DESC,
                            candidate.version DESC
                        LIMIT 1
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
                LEFT JOIN metric_sku_scopes
                    ON metric_sku_scopes.sku_id=metric.sku_id
                LEFT JOIN daily_order_costs
                    ON daily_order_costs.platform=metric.platform
                    AND daily_order_costs.canonical_store=metric.store
                    AND daily_order_costs.order_date=metric.data_date
                LEFT JOIN expected_order_costs
                    ON expected_order_costs.platform=metric.platform
                    AND expected_order_costs.canonical_store=metric.store
                    AND expected_order_costs.order_date=metric.data_date
                WHERE metric.data_date = ?
                ORDER BY metric.platform, metric.campaign_id, metric.sku_id
                """,
                (data_date.isoformat(),) * 3,
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

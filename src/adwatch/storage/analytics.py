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
                    campaign.baseline_budget
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
                WHERE metric.data_date = ?
                ORDER BY metric.platform, metric.campaign_id, metric.sku_id
                """,
                (data_date.isoformat(),),
            ).fetchall()

    @staticmethod
    def decimal(row: sqlite3.Row, key: str) -> Decimal | None:
        value = row[key]
        return None if value is None else Decimal(value)

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from adwatch.orders.models import PlatformOrderLine, PlatformSku
from adwatch.storage.db import Database


class OrderRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_skus(self, skus: Iterable[PlatformSku]) -> int:
        items = tuple(skus)
        with self.database.transaction() as connection:
            for item in items:
                connection.execute(
                    """
                    INSERT INTO platform_sku_mappings(
                        platform, store, item_id, model_id, seller_sku,
                        variation_name, product_name, inventory_units,
                        observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, store, item_id, model_id)
                    DO UPDATE SET
                        seller_sku=excluded.seller_sku,
                        variation_name=excluded.variation_name,
                        product_name=excluded.product_name,
                        inventory_units=excluded.inventory_units,
                        observed_at=MIN(
                            platform_sku_mappings.observed_at,
                            excluded.observed_at
                        )
                    """,
                    (
                        item.platform,
                        item.store,
                        item.item_id,
                        item.model_id,
                        item.seller_sku,
                        item.variation_name,
                        item.product_name,
                        item.inventory_units,
                        item.observed_at.isoformat(),
                    ),
                )
        return len(items)

    def upsert_orders(self, orders: Iterable[PlatformOrderLine]) -> int:
        items = tuple(orders)
        with self.database.transaction() as connection:
            for item in items:
                connection.execute(
                    """
                    INSERT INTO platform_order_lines(
                        platform, store, order_id, item_id, model_id,
                        seller_sku, variation_name, product_name, quantity,
                        buyer_paid, currency, order_status, logistics_status,
                        refund_status, ordered_at, source_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, store, order_id, item_id, model_id)
                    DO UPDATE SET
                        quantity=excluded.quantity,
                        buyer_paid=excluded.buyer_paid,
                        order_status=excluded.order_status,
                        logistics_status=CASE
                            WHEN excluded.logistics_status=''
                            THEN platform_order_lines.logistics_status
                            ELSE excluded.logistics_status END,
                        refund_status=excluded.refund_status,
                        source_updated_at=excluded.source_updated_at
                    """,
                    (
                        item.platform,
                        item.store,
                        item.order_id,
                        item.item_id,
                        item.model_id,
                        item.seller_sku,
                        item.variation_name,
                        item.product_name,
                        item.quantity,
                        str(item.buyer_paid),
                        item.currency,
                        item.order_status,
                        item.logistics_status,
                        item.refund_status,
                        item.ordered_at.isoformat(),
                        item.source_updated_at.isoformat(),
                    ),
                )
        return len(items)

    def pending_sku_costs(self):
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT
                    sku.platform, sku.store, sku.product_name,
                    sku.item_id, sku.model_id, sku.seller_sku,
                    sku.variation_name, sku.inventory_units,
                    substr(sku.observed_at, 1, 10) AS first_seen_date,
                    MAX(orders.ordered_at) AS latest_order_date,
                    COUNT(DISTINCT orders.order_id) AS pending_orders,
                    COALESCE(SUM(orders.quantity), 0) AS pending_units
                FROM platform_sku_mappings AS sku
                LEFT JOIN platform_order_lines AS orders
                  ON orders.platform=sku.platform
                 AND orders.store=sku.store
                 AND orders.item_id=sku.item_id
                 AND orders.model_id=sku.model_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM sku_cost_history AS cost
                    WHERE cost.platform=sku.platform
                      AND cost.store=sku.store
                      AND cost.seller_sku=sku.seller_sku
                )
                GROUP BY
                    sku.platform, sku.store, sku.product_name,
                    sku.item_id, sku.model_id, sku.seller_sku,
                    sku.variation_name, sku.inventory_units,
                    first_seen_date
                ORDER BY sku.store, sku.product_name, sku.variation_name
                """
            ).fetchall()

    def set_sku_cost(
        self,
        *,
        platform: str,
        store: str,
        seller_sku: str,
        effective_date: date,
        unit_cost_cny: Decimal,
        note: str = "",
    ) -> None:
        if unit_cost_cny <= 0:
            raise ValueError("unit cost must be positive")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sku_cost_history(
                    platform, store, seller_sku, effective_date,
                    unit_cost_cny, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, store, seller_sku, effective_date)
                DO UPDATE SET unit_cost_cny=excluded.unit_cost_cny,
                              note=excluded.note,
                              updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    platform,
                    store,
                    seller_sku,
                    effective_date.isoformat(),
                    str(unit_cost_cny),
                    note,
                ),
            )

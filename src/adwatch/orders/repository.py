from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
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
                        seller_sku=excluded.seller_sku,
                        variation_name=excluded.variation_name,
                        product_name=excluded.product_name,
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
                WITH mapped AS (
                    SELECT
                        sku.*,
                        COALESCE(alias.canonical_store, sku.store)
                            AS canonical_store,
                        (
                            SELECT COUNT(DISTINCT
                                CASE
                                    WHEN TRIM(COALESCE(
                                        other.product_name, ''
                                    )) <> ''
                                      OR TRIM(COALESCE(
                                          other.variation_name, ''
                                      )) <> ''
                                    THEN LOWER(TRIM(COALESCE(
                                        other.product_name, ''
                                    ))) || char(31) ||
                                         LOWER(TRIM(COALESCE(
                                             other.variation_name, ''
                                         )))
                                    ELSE other.item_id || char(31) ||
                                         other.model_id
                                END
                            )
                            FROM platform_sku_mappings AS other
                            LEFT JOIN store_aliases AS other_alias
                              ON other_alias.platform=other.platform
                             AND other_alias.source_store=other.store
                            WHERE other.platform=sku.platform
                              AND COALESCE(
                                  other_alias.canonical_store, other.store
                              )=COALESCE(
                                  alias.canonical_store, sku.store
                              )
                              AND other.seller_sku=sku.seller_sku
                        ) AS identity_count
                    FROM platform_sku_mappings AS sku
                    LEFT JOIN store_aliases AS alias
                      ON alias.platform=sku.platform
                     AND alias.source_store=sku.store
                ),
                identified AS (
                    SELECT
                        mapped.*,
                        CASE
                            WHEN identity_count > 1
                            THEN seller_sku || ' [' || item_id || ':'
                                 || model_id || ']'
                            ELSE seller_sku
                        END AS sku_identity
                    FROM mapped
                )
                SELECT
                    sku.platform, sku.store, sku.product_name,
                    sku.item_id, sku.model_id,
                    sku.sku_identity AS seller_sku,
                    sku.variation_name, sku.inventory_units,
                    substr(sku.observed_at, 1, 10) AS first_seen_date,
                    MAX(orders.ordered_at) AS latest_order_date,
                    COUNT(DISTINCT orders.order_id) AS pending_orders,
                    COALESCE(SUM(orders.quantity), 0) AS pending_units
                FROM identified AS sku
                LEFT JOIN platform_order_lines AS orders
                  ON orders.platform=sku.platform
                 AND orders.store=sku.store
                 AND orders.item_id=sku.item_id
                 AND orders.model_id=sku.model_id
                 AND LOWER(TRIM(COALESCE(orders.order_status, '')))
                     NOT IN ('cancelled', 'canceled')
                WHERE NOT EXISTS (
                    SELECT 1 FROM sku_cost_history AS cost
                    LEFT JOIN store_aliases AS cost_alias
                      ON cost_alias.platform=cost.platform
                     AND cost_alias.source_store=cost.store
                    WHERE cost.platform=sku.platform
                      AND COALESCE(
                          cost_alias.canonical_store, cost.store
                      )=sku.canonical_store
                      AND cost.seller_sku=sku.sku_identity
                )
                  AND orders.order_id IS NOT NULL
                GROUP BY
                    sku.platform, sku.store, sku.product_name,
                    sku.item_id, sku.model_id, sku.sku_identity,
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

    def sku_map(
        self,
        *,
        platform: str,
        store: str,
    ) -> dict[tuple[str, str], tuple[str, str, str]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT item_id, model_id, seller_sku,
                       variation_name, product_name
                FROM platform_sku_mappings
                WHERE platform=? AND store=?
                """,
                (platform, store),
            ).fetchall()
        return {
            (str(row["item_id"]), str(row["model_id"])): (
                str(row["seller_sku"]),
                str(row["variation_name"]),
                str(row["product_name"]),
            )
            for row in rows
        }

    def start_sync_run(
        self,
        *,
        run_id: str,
        platform: str,
        store: str,
        started_at: datetime,
        start_date: date,
        end_date: date,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO order_sync_runs(
                    id, platform, store, started_at, start_date, end_date,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, 'running')
                """,
                (
                    run_id,
                    platform,
                    store,
                    started_at.isoformat(),
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            )

    def finish_sync_run(
        self,
        *,
        run_id: str,
        finished_at: datetime,
        status: str,
        order_count: int,
        sku_count: int,
        rejected_count: int,
        refund_count: int = 0,
        logistics_count: int = 0,
        inventory_snapshot_count: int = 0,
        product_rejected_count: int = 0,
        order_rejected_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE order_sync_runs
                SET finished_at=?, status=?, order_count=?, sku_count=?,
                rejected_count=?, product_rejected_count=?,
                order_rejected_count=?, refund_count=?, logistics_count=?,
                inventory_snapshot_count=?, error_message=?
                WHERE id=?
                """,
                (
                    finished_at.isoformat(),
                    status,
                    order_count,
                    sku_count,
                    rejected_count,
                    product_rejected_count,
                    order_rejected_count,
                    refund_count,
                    logistics_count,
                    inventory_snapshot_count,
                    error_message,
                    run_id,
                ),
            )

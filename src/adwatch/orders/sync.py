from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from adwatch.inventory.service import InventoryError, InventoryService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.storage.db import Database
from adwatch.storage.store_aliases import equivalent_store_names


@dataclass(frozen=True)
class OperationsSyncResult:
    shipped: int = 0
    returned: int = 0
    cancelled: int = 0
    pending_cost: int = 0
    pending_inventory: int = 0
    pending_fulfillment: int = 0
    pending_order_identity: int = 0
    supplier_costed: int = 0
    unchanged: int = 0


class OperationsSyncService:
    _CANCELLED = frozenset({"cancelled", "canceled"})
    _RETURNED = frozenset(
        {
            "returned",
            "refund_completed",
            "refunded",
            "return_completed",
        }
    )
    _FULFILLED = frozenset({"shipped", "delivered", "completed", "complete"})

    def __init__(self, database: Database) -> None:
        self.database = database
        self.inventory = InventoryService(database)
        self.fulfillment = FulfillmentService(database)

    def sync(self) -> OperationsSyncResult:
        counts = {
            "shipped": 0,
            "returned": 0,
            "cancelled": 0,
            "pending_cost": 0,
            "pending_inventory": 0,
            "pending_fulfillment": 0,
            "pending_order_identity": 0,
            "supplier_costed": 0,
            "unchanged": 0,
        }
        for row in self._order_lines():
            if row["resolution_status"] != "ready":
                counts["pending_order_identity"] += 1
                continue
            order_status = row["order_status"].strip().lower()
            logistics_status = row["logistics_status"].strip().lower()
            refund_status = row["refund_status"].strip().lower()
            if order_status in self._CANCELLED:
                counts["cancelled"] += 1
                continue
            ordered_on = date.fromisoformat(row["ordered_on"])
            fulfillment = self.fulfillment.snapshot_order(
                platform=row["platform"],
                store=row["store"],
                order_id=row["order_id"],
                seller_sku=row["seller_sku"],
                ordered_on=ordered_on,
            )
            if fulfillment is None:
                counts["pending_fulfillment"] += 1
                continue
            if refund_status in self._RETURNED:
                if fulfillment.mode == "supplier_fulfilled":
                    cost = self._cost_for(
                        row["platform"],
                        row["store"],
                        row["seller_sku"],
                        row["ordered_on"],
                        order_id=row["order_id"],
                        raw_seller_sku=row["raw_seller_sku"],
                        product_name=row["product_name"],
                        variation_name=row["variation_name"],
                    )
                    if cost is None:
                        counts["pending_cost"] += 1
                        continue
                    if self.inventory.record_order_cost(
                        platform=row["platform"],
                        store=row["store"],
                        order_id=row["order_id"],
                        seller_sku=row["seller_sku"],
                        quantity=int(row["quantity"]),
                        unit_cost_cny=Decimal(cost["unit_cost_cny"]),
                        cost_effective_date=date.fromisoformat(
                            cost["effective_date"]
                        ),
                        status="returned",
                        legacy_seller_sku=row["raw_seller_sku"],
                    ):
                        counts["returned"] += 1
                    else:
                        counts["unchanged"] += 1
                    continue
                if self.inventory.return_order(
                    platform=row["platform"],
                    store=row["store"],
                    order_id=row["order_id"],
                    seller_sku=row["seller_sku"],
                    quantity=int(row["quantity"]),
                    returned_on=date.fromisoformat(row["event_date"]),
                ):
                    counts["returned"] += 1
                else:
                    counts["unchanged"] += 1
                continue
            cost = self._cost_for(
                row["platform"],
                row["store"],
                row["seller_sku"],
                row["ordered_on"],
                order_id=row["order_id"],
                raw_seller_sku=row["raw_seller_sku"],
                product_name=row["product_name"],
                variation_name=row["variation_name"],
            )
            if cost is None:
                counts["pending_cost"] += 1
                continue
            if fulfillment.mode == "supplier_fulfilled":
                changed = self.inventory.record_order_cost(
                    platform=row["platform"],
                    store=row["store"],
                    order_id=row["order_id"],
                    seller_sku=row["seller_sku"],
                    quantity=int(row["quantity"]),
                    unit_cost_cny=Decimal(cost["unit_cost_cny"]),
                    cost_effective_date=date.fromisoformat(
                        cost["effective_date"]
                    ),
                    legacy_seller_sku=row["raw_seller_sku"],
                )
                counts["supplier_costed" if changed else "unchanged"] += 1
                continue
            if (
                order_status not in self._FULFILLED
                and logistics_status not in self._FULFILLED
            ):
                counts["unchanged"] += 1
                continue
            try:
                changed = self.inventory.ship_order(
                    platform=row["platform"],
                    store=row["store"],
                    order_id=row["order_id"],
                    seller_sku=row["seller_sku"],
                    quantity=int(row["quantity"]),
                    shipped_on=date.fromisoformat(row["event_date"]),
                    unit_cost_cny=Decimal(cost["unit_cost_cny"]),
                    order_status=row["order_status"],
                    cost_effective_date=date.fromisoformat(cost["effective_date"]),
                )
            except InventoryError:
                counts["pending_inventory"] += 1
            else:
                counts["shipped" if changed else "unchanged"] += 1
        return OperationsSyncResult(**counts)

    def _order_lines(self):
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT platform, canonical_store AS store, order_id,
                       sku_identity AS seller_sku,
                       raw_seller_sku,
                       product_name, variation_name,
                       quantity,
                       substr(ordered_at, 1, 10) AS ordered_on,
                       substr(source_updated_at, 1, 10) AS event_date,
                       order_status, logistics_status, refund_status,
                       resolution_status
                FROM effective_order_sku_lines
                ORDER BY ordered_on, order_id, seller_sku
                """
            ).fetchall()

    def _cost_for(
        self,
        platform: str,
        store: str,
        seller_sku: str,
        ordered_on: str,
        *,
        order_id: str,
        raw_seller_sku: str,
        product_name: str,
        variation_name: str,
    ):
        with self.database.connect() as connection:
            stores = equivalent_store_names(
                connection, platform=platform, store=store
            )
            placeholders = ", ".join("?" for _ in stores)
            exact = connection.execute(
                f"""
                SELECT effective_date, unit_cost_cny
                FROM sku_cost_history
                WHERE platform=? AND store IN ({placeholders})
                  AND seller_sku=?
                  AND effective_date<=?
                ORDER BY CASE WHEN store=? THEN 0 ELSE 1 END,
                         effective_date DESC, store
                LIMIT 1
                """,
                (
                    platform.lower(),
                    *stores,
                    seller_sku,
                    ordered_on,
                    store,
                ),
            ).fetchone()
            if exact is not None or raw_seller_sku == seller_sku:
                return exact
            frozen = connection.execute(
                f"""
                SELECT snapshot.cost_effective_date AS effective_date,
                       snapshot.unit_cost_cny
                FROM order_cost_snapshots AS snapshot
                WHERE snapshot.platform=?
                  AND snapshot.store IN ({placeholders})
                  AND snapshot.order_id=?
                  AND snapshot.seller_sku=?
                  AND snapshot.status IN ('confirmed', 'returned')
                  AND snapshot.cost_effective_date IS NOT NULL
                  AND 1=(
                      SELECT COUNT(*)
                      FROM effective_order_sku_lines AS candidate
                      WHERE candidate.platform=?
                        AND candidate.canonical_store=?
                        AND candidate.order_id=?
                        AND candidate.raw_seller_sku=?
                        AND candidate.resolution_status='ready'
                  )
                ORDER BY snapshot.created_at DESC, snapshot.store
                LIMIT 1
                """,
                (
                    platform.lower(),
                    *stores,
                    order_id,
                    raw_seller_sku,
                    platform.lower(),
                    store,
                    order_id,
                    raw_seller_sku,
                ),
            ).fetchone()
            if frozen is not None:
                return frozen
            return connection.execute(
                f"""
                SELECT cost.effective_date, cost.unit_cost_cny
                FROM sku_cost_history AS cost
                WHERE cost.platform=?
                  AND cost.store IN ({placeholders})
                  AND cost.seller_sku=?
                  AND cost.effective_date<=?
                  AND EXISTS (
                      SELECT 1
                      FROM platform_sku_mappings AS legacy
                      WHERE legacy.platform=cost.platform
                        AND legacy.store=cost.store
                        AND legacy.seller_sku=cost.seller_sku
                        AND LOWER(TRIM(COALESCE(legacy.product_name, '')))
                            =LOWER(TRIM(?))
                        AND LOWER(TRIM(COALESCE(legacy.variation_name, '')))
                            =LOWER(TRIM(?))
                  )
                  AND 1=(
                      SELECT COUNT(DISTINCT
                          LOWER(TRIM(COALESCE(legacy.product_name, '')))
                          || char(31) ||
                          LOWER(TRIM(COALESCE(legacy.variation_name, '')))
                      )
                      FROM platform_sku_mappings AS legacy
                      WHERE legacy.platform=cost.platform
                        AND legacy.store=cost.store
                        AND legacy.seller_sku=cost.seller_sku
                  )
                ORDER BY CASE WHEN cost.store=? THEN 0 ELSE 1 END,
                         cost.effective_date DESC, cost.store
                LIMIT 1
                """,
                (
                    platform.lower(),
                    *stores,
                    raw_seller_sku,
                    ordered_on,
                    product_name,
                    variation_name,
                    store,
                ),
            ).fetchone()

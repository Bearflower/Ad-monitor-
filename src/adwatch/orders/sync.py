from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from adwatch.inventory.service import InventoryError, InventoryService
from adwatch.storage.db import Database


@dataclass(frozen=True)
class OperationsSyncResult:
    shipped: int = 0
    returned: int = 0
    cancelled: int = 0
    pending_cost: int = 0
    pending_inventory: int = 0
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

    def sync(self) -> OperationsSyncResult:
        counts = {
            "shipped": 0,
            "returned": 0,
            "cancelled": 0,
            "pending_cost": 0,
            "pending_inventory": 0,
            "unchanged": 0,
        }
        for row in self._order_lines():
            order_status = row["order_status"].strip().lower()
            logistics_status = row["logistics_status"].strip().lower()
            refund_status = row["refund_status"].strip().lower()
            if order_status in self._CANCELLED:
                counts["cancelled"] += 1
                continue
            if refund_status in self._RETURNED:
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
            if (
                order_status not in self._FULFILLED
                and logistics_status not in self._FULFILLED
            ):
                counts["unchanged"] += 1
                continue
            cost = self._cost_for(
                row["platform"],
                row["store"],
                row["seller_sku"],
                row["ordered_on"],
            )
            if cost is None:
                counts["pending_cost"] += 1
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
                SELECT platform, store, order_id, seller_sku,
                       SUM(quantity) AS quantity,
                       MIN(substr(ordered_at, 1, 10)) AS ordered_on,
                       MAX(substr(source_updated_at, 1, 10)) AS event_date,
                       MAX(order_status) AS order_status,
                       MAX(logistics_status) AS logistics_status,
                       MAX(refund_status) AS refund_status
                FROM platform_order_lines
                GROUP BY platform, store, order_id, seller_sku
                ORDER BY ordered_on, order_id, seller_sku
                """
            ).fetchall()

    def _cost_for(
        self,
        platform: str,
        store: str,
        seller_sku: str,
        ordered_on: str,
    ):
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT effective_date, unit_cost_cny
                FROM sku_cost_history
                WHERE platform=? AND store=? AND seller_sku=?
                  AND effective_date<=?
                ORDER BY effective_date DESC LIMIT 1
                """,
                (platform, store, seller_sku, ordered_on),
            ).fetchone()

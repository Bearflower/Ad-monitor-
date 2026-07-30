from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.inventory.models import PurchaseLine
from adwatch.storage.db import Database


class InventoryError(ValueError):
    pass


class InventoryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def receive_purchase(
        self,
        *,
        receipt_id: str,
        supplier: str,
        received_on: date,
        lines: tuple[PurchaseLine, ...],
        actor: str,
    ) -> bool:
        if not lines or any(
            line.quantity <= 0 or line.unit_cost_cny <= 0 for line in lines
        ):
            raise InventoryError("purchase lines must be positive")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM purchase_receipts WHERE id=?", (receipt_id,)
            ).fetchone():
                return False
            connection.execute(
                """
                INSERT INTO purchase_receipts(
                    id, supplier, received_on, status, created_by, created_at
                ) VALUES (?, ?, ?, 'confirmed', ?, ?)
                """,
                (receipt_id, supplier, received_on.isoformat(), actor, now),
            )
            for line in lines:
                connection.execute(
                    """
                    INSERT INTO purchase_lines(
                        receipt_id, seller_sku, quantity,
                        unit_cost_cny, line_cost_cny
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        line.seller_sku,
                        line.quantity,
                        str(line.unit_cost_cny),
                        str(line.unit_cost_cny * line.quantity),
                    ),
                )
                self._move(
                    connection,
                    line.seller_sku,
                    "purchase_in",
                    line.quantity,
                    received_on,
                    "purchase",
                    receipt_id,
                    "",
                    now,
                )
        return True

    def ship_order(
        self,
        *,
        platform: str,
        store: str,
        order_id: str,
        seller_sku: str,
        quantity: int,
        shipped_on: date,
        unit_cost_cny: Decimal,
        order_status: str = "ready_to_ship",
        cost_effective_date: date | None = None,
    ) -> bool:
        if order_status.lower() in {"cancelled", "canceled"}:
            return False
        source_id = f"{platform}:{store}:{order_id}"
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            if connection.execute(
                """
                SELECT 1 FROM inventory_movements
                WHERE source_type='order' AND source_id=?
                  AND seller_sku=? AND movement_type='sale_out'
                """,
                (source_id, seller_sku),
            ).fetchone():
                return False
            if self._balance(connection, seller_sku) < quantity:
                raise InventoryError("insufficient inventory")
            self._move(
                connection,
                seller_sku,
                "sale_out",
                -quantity,
                shipped_on,
                "order",
                source_id,
                "",
                now,
            )
            connection.execute(
                """
                INSERT INTO order_cost_snapshots(
                    platform, store, order_id, seller_sku, quantity,
                    unit_cost_cny, total_cost_cny, cost_effective_date,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
                """,
                (
                    platform,
                    store,
                    order_id,
                    seller_sku,
                    quantity,
                    str(unit_cost_cny),
                    str(unit_cost_cny * quantity),
                    (
                        None
                        if cost_effective_date is None
                        else cost_effective_date.isoformat()
                    ),
                    now,
                ),
            )
        return True

    def record_order_cost(
        self,
        *,
        platform: str,
        store: str,
        order_id: str,
        seller_sku: str,
        quantity: int,
        unit_cost_cny: Decimal,
        cost_effective_date: date,
        status: str = "confirmed",
    ) -> bool:
        if quantity <= 0 or unit_cost_cny <= 0:
            raise InventoryError("order cost inputs must be positive")
        if status not in {"confirmed", "returned"}:
            raise InventoryError("invalid order cost status")
        with self.database.transaction() as connection:
            if connection.execute(
                """
                SELECT 1 FROM order_cost_snapshots
                WHERE platform=? AND store=? AND order_id=? AND seller_sku=?
                """,
                (platform, store, order_id, seller_sku),
            ).fetchone():
                return False
            connection.execute(
                """
                INSERT INTO order_cost_snapshots(
                    platform, store, order_id, seller_sku, quantity,
                    unit_cost_cny, total_cost_cny, cost_effective_date,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (
                    platform,
                    store,
                    order_id,
                    seller_sku,
                    quantity,
                    str(unit_cost_cny),
                    str(unit_cost_cny * quantity),
                    cost_effective_date.isoformat(),
                    status,
                ),
            )
        return True

    def return_order(
        self,
        *,
        platform: str,
        store: str,
        order_id: str,
        seller_sku: str,
        quantity: int,
        returned_on: date,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        source_id = f"{platform}:{store}:{order_id}"
        with self.database.transaction() as connection:
            if not connection.execute(
                """
                SELECT 1 FROM inventory_movements
                WHERE source_type='order' AND source_id=?
                  AND seller_sku=? AND movement_type='sale_out'
                """,
                (source_id, seller_sku),
            ).fetchone():
                return False
            if connection.execute(
                """
                SELECT 1 FROM inventory_movements
                WHERE source_type='order_return' AND source_id=?
                  AND seller_sku=? AND movement_type='sale_return'
                """,
                (source_id, seller_sku),
            ).fetchone():
                return False
            self._move(
                connection,
                seller_sku,
                "sale_return",
                quantity,
                returned_on,
                "order_return",
                source_id,
                "",
                now,
            )
            connection.execute(
                """
                UPDATE order_cost_snapshots SET status='returned'
                WHERE platform=? AND store=? AND order_id=? AND seller_sku=?
                """,
                (platform, store, order_id, seller_sku),
            )
        return True

    def cancel_order_cost(
        self,
        *,
        platform: str,
        store: str,
        order_id: str,
        seller_sku: str,
    ) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE order_cost_snapshots
                SET status='cancelled'
                WHERE platform=? AND store=? AND order_id=? AND seller_sku=?
                  AND status!='cancelled'
                """,
                (platform, store, order_id, seller_sku),
            )
        return cursor.rowcount > 0

    def damage(
        self,
        *,
        seller_sku: str,
        quantity: int,
        occurred_on: date,
        reason: str,
        actor: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            if self._balance(connection, seller_sku) < quantity:
                raise InventoryError("insufficient inventory")
            self._move(
                connection,
                seller_sku,
                "damage",
                -quantity,
                occurred_on,
                "manual",
                str(uuid.uuid4()),
                f"{actor}: {reason}",
                now,
            )

    def balance(self, seller_sku: str) -> int:
        with self.database.connect() as connection:
            return self._balance(connection, seller_sku)

    @staticmethod
    def _balance(connection, seller_sku: str) -> int:
        row = connection.execute(
            "SELECT units FROM inventory_balances WHERE seller_sku=?",
            (seller_sku,),
        ).fetchone()
        return 0 if row is None else int(row["units"])

    @classmethod
    def _move(
        cls,
        connection,
        seller_sku: str,
        movement_type: str,
        quantity_delta: int,
        occurred_on: date,
        source_type: str,
        source_id: str,
        note: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO inventory_movements(
                id, seller_sku, movement_type, quantity_delta,
                occurred_on, source_type, source_id, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                seller_sku,
                movement_type,
                quantity_delta,
                occurred_on.isoformat(),
                source_type,
                source_id,
                note,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO inventory_balances(seller_sku, units, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(seller_sku) DO UPDATE SET
              units=inventory_balances.units + excluded.units,
              updated_at=excluded.updated_at
            """,
            (seller_sku, quantity_delta, now),
        )

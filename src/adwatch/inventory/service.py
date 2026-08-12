from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.inventory.models import PurchaseLine
from adwatch.storage.db import Database
from adwatch.storage.store_aliases import equivalent_store_names


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
        supplier = self._required(supplier, "supplier")
        actor = self._required(actor, "actor")
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
                    actor=actor,
                )
            self._audit(
                connection,
                "purchase_receipt",
                receipt_id,
                "create",
                actor,
                None,
                None,
                {"status": "confirmed", "line_count": len(lines)},
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
            stores = equivalent_store_names(
                connection, platform=platform, store=store
            )
            source_ids = tuple(
                f"{platform}:{candidate}:{order_id}" for candidate in stores
            )
            placeholders = ", ".join("?" for _ in source_ids)
            if connection.execute(
                f"""
                SELECT 1 FROM inventory_movements
                WHERE source_type='order' AND source_id IN ({placeholders})
                  AND seller_sku=? AND movement_type='sale_out'
                """,
                (*source_ids, seller_sku),
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
        legacy_seller_sku: str | None = None,
    ) -> bool:
        if quantity <= 0 or unit_cost_cny <= 0:
            raise InventoryError("order cost inputs must be positive")
        if status not in {"confirmed", "returned"}:
            raise InventoryError("invalid order cost status")
        with self.database.transaction() as connection:
            stores = equivalent_store_names(
                connection, platform=platform, store=store
            )
            placeholders = ", ".join("?" for _ in stores)
            existing = connection.execute(
                f"""
                SELECT store, quantity, unit_cost_cny, cost_effective_date,
                       status
                FROM order_cost_snapshots
                WHERE platform=? AND store IN ({placeholders})
                  AND order_id=? AND seller_sku=?
                ORDER BY created_at DESC, store
                """,
                (platform, *stores, order_id, seller_sku),
            ).fetchall()
            if not existing and legacy_seller_sku not in {None, seller_sku}:
                legacy = connection.execute(
                    f"""
                    SELECT store, quantity, unit_cost_cny,
                           cost_effective_date, status
                    FROM order_cost_snapshots
                    WHERE platform=? AND store IN ({placeholders})
                      AND order_id=? AND seller_sku=?
                    ORDER BY created_at DESC, store
                    """,
                    (platform, *stores, order_id, legacy_seller_sku),
                ).fetchall()
                if len(legacy) == 1:
                    identity_count = connection.execute(
                        """
                        SELECT COUNT(DISTINCT sku_identity)
                        FROM effective_order_sku_lines
                        WHERE platform=? AND canonical_store=?
                          AND order_id=? AND raw_seller_sku=?
                          AND resolution_status='ready'
                        """,
                        (platform, store, order_id, legacy_seller_sku),
                    ).fetchone()[0]
                    if int(identity_count or 0) != 1:
                        return False
                    connection.execute(
                        """
                        UPDATE order_cost_snapshots
                        SET seller_sku=?
                        WHERE platform=? AND store=? AND order_id=?
                          AND seller_sku=?
                        """,
                        (
                            seller_sku,
                            platform,
                            legacy[0]["store"],
                            order_id,
                            legacy_seller_sku,
                        ),
                    )
                    existing = legacy
            if existing:
                if len(existing) != 1:
                    return False
                snapshot = existing[0]
                if int(snapshot["quantity"]) >= quantity:
                    return False
                if (
                    snapshot["status"] != status
                    or Decimal(snapshot["unit_cost_cny"]) != unit_cost_cny
                    or snapshot["cost_effective_date"]
                    != cost_effective_date.isoformat()
                ):
                    return False
                connection.execute(
                    """
                    UPDATE order_cost_snapshots
                    SET quantity=?, total_cost_cny=?
                    WHERE platform=? AND store=? AND order_id=?
                      AND seller_sku=?
                    """,
                    (
                        quantity,
                        str(unit_cost_cny * quantity),
                        platform,
                        snapshot["store"],
                        order_id,
                        seller_sku,
                    ),
                )
                return True
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
            stores = equivalent_store_names(
                connection, platform=platform, store=store
            )
            source_ids = tuple(
                f"{platform}:{candidate}:{order_id}" for candidate in stores
            )
            source_placeholders = ", ".join("?" for _ in source_ids)
            store_placeholders = ", ".join("?" for _ in stores)
            if not connection.execute(
                f"""
                SELECT 1 FROM inventory_movements
                WHERE source_type='order' AND source_id IN ({source_placeholders})
                  AND seller_sku=? AND movement_type='sale_out'
                """,
                (*source_ids, seller_sku),
            ).fetchone():
                return False
            if connection.execute(
                f"""
                SELECT 1 FROM inventory_movements
                WHERE source_type='order_return'
                  AND source_id IN ({source_placeholders})
                  AND seller_sku=? AND movement_type='sale_return'
                """,
                (*source_ids, seller_sku),
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
                f"""
                UPDATE order_cost_snapshots SET status='returned'
                WHERE platform=? AND store IN ({store_placeholders})
                  AND order_id=? AND seller_sku=?
                """,
                (platform, *stores, order_id, seller_sku),
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
    ) -> str:
        seller_sku = self._required(seller_sku, "seller sku")
        reason = self._required(reason, "reason")
        actor = self._required(actor, "actor")
        if quantity <= 0:
            raise InventoryError("damage quantity must be positive")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            if self._balance(connection, seller_sku) < quantity:
                raise InventoryError("insufficient inventory")
            movement_id = self._move(
                connection,
                seller_sku,
                "damage",
                -quantity,
                occurred_on,
                "manual",
                str(uuid.uuid4()),
                reason,
                now,
                actor=actor,
                reason=reason,
            )
            self._audit(
                connection,
                "inventory_movement",
                movement_id,
                "create",
                actor,
                reason,
                None,
                {"movement_type": "damage", "quantity_delta": -quantity},
                now,
            )
        return movement_id

    def adjust_inventory(
        self,
        *,
        seller_sku: str,
        quantity_delta: int,
        occurred_on: date,
        reason: str,
        actor: str,
    ) -> str:
        seller_sku = self._required(seller_sku, "seller sku")
        reason = self._required(reason, "reason")
        actor = self._required(actor, "actor")
        if quantity_delta == 0:
            raise InventoryError("quantity delta must be non-zero")
        now = datetime.now(UTC).isoformat()
        source_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            if self._balance(connection, seller_sku) + quantity_delta < 0:
                raise InventoryError("insufficient inventory")
            movement_id = self._move(
                connection,
                seller_sku,
                "inventory_adjustment",
                quantity_delta,
                occurred_on,
                "manual_adjustment",
                source_id,
                reason,
                now,
                actor=actor,
                reason=reason,
            )
            self._audit(
                connection,
                "inventory_movement",
                movement_id,
                "create",
                actor,
                reason,
                None,
                {
                    "movement_type": "inventory_adjustment",
                    "quantity_delta": quantity_delta,
                },
                now,
            )
        return movement_id

    def reverse_movement(
        self,
        movement_id: str,
        *,
        occurred_on: date,
        reason: str,
        actor: str,
    ) -> str:
        reason = self._required(reason, "reversal reason")
        actor = self._required(actor, "actor")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            original = connection.execute(
                "SELECT * FROM inventory_movements WHERE id=?",
                (movement_id,),
            ).fetchone()
            if original is None or original["movement_type"] not in {
                "damage",
                "inventory_adjustment",
            }:
                raise InventoryError("movement cannot be reversed")
            if original["reversal_of"] is not None:
                raise InventoryError("reversal movement cannot be reversed")
            exists = connection.execute(
                "SELECT 1 FROM inventory_movements WHERE reversal_of=?",
                (movement_id,),
            ).fetchone()
            if exists is not None:
                raise InventoryError("movement already reversed")
            quantity_delta = -int(original["quantity_delta"])
            if self._balance(connection, original["seller_sku"]) + quantity_delta < 0:
                raise InventoryError("insufficient inventory")
            reversal_id = self._move(
                connection,
                original["seller_sku"],
                f"{original['movement_type']}_reversal",
                quantity_delta,
                occurred_on,
                "manual_reversal",
                movement_id,
                reason,
                now,
                actor=actor,
                reason=reason,
                reversal_of=movement_id,
            )
            self._audit(
                connection,
                "inventory_movement",
                movement_id,
                "reverse",
                actor,
                reason,
                {"quantity_delta": int(original["quantity_delta"])},
                {"reversal_id": reversal_id},
                now,
            )
            self._audit(
                connection,
                "inventory_movement",
                reversal_id,
                "create",
                actor,
                reason,
                None,
                {
                    "quantity_delta": quantity_delta,
                    "reversal_of": movement_id,
                },
                now,
            )
        return reversal_id

    def reverse_purchase(
        self,
        receipt_id: str,
        *,
        occurred_on: date,
        reason: str,
        actor: str,
    ) -> tuple[str, ...]:
        reason = self._required(reason, "reversal reason")
        actor = self._required(actor, "actor")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            receipt = connection.execute(
                "SELECT * FROM purchase_receipts WHERE id=?",
                (receipt_id,),
            ).fetchone()
            if receipt is None or receipt["status"] != "confirmed":
                raise InventoryError("only confirmed purchases can be reversed")
            lines = connection.execute(
                """
                SELECT line.seller_sku, line.quantity,
                       movement.id AS movement_id
                FROM purchase_lines AS line
                LEFT JOIN inventory_movements AS movement
                  ON movement.source_type='purchase'
                 AND movement.source_id=line.receipt_id
                 AND movement.seller_sku=line.seller_sku
                 AND movement.movement_type='purchase_in'
                WHERE line.receipt_id=?
                """,
                (receipt_id,),
            ).fetchall()
            if not lines or any(row["movement_id"] is None for row in lines):
                raise InventoryError("purchase inventory facts are incomplete")
            if any(
                self._balance(connection, row["seller_sku"])
                < int(row["quantity"])
                for row in lines
            ):
                raise InventoryError("insufficient inventory")
            connection.execute(
                """
                UPDATE purchase_receipts
                SET status='reversed', reversal_reason=? WHERE id=?
                """,
                (reason, receipt_id),
            )
            reversal_ids = tuple(
                self._move(
                    connection,
                    row["seller_sku"],
                    "purchase_reversal",
                    -int(row["quantity"]),
                    occurred_on,
                    "purchase_reversal",
                    receipt_id,
                    reason,
                    now,
                    actor=actor,
                    reason=reason,
                    reversal_of=row["movement_id"],
                )
                for row in lines
            )
            for row, reversal_id in zip(lines, reversal_ids, strict=True):
                self._audit(
                    connection,
                    "inventory_movement",
                    reversal_id,
                    "create",
                    actor,
                    reason,
                    None,
                    {
                        "quantity_delta": -int(row["quantity"]),
                        "reversal_of": row["movement_id"],
                    },
                    now,
                )
            self._audit(
                connection,
                "purchase_receipt",
                receipt_id,
                "reverse",
                actor,
                reason,
                {"status": "confirmed"},
                {"status": "reversed", "movement_ids": reversal_ids},
                now,
            )
        return reversal_ids

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
        *,
        actor: str = "system",
        reason: str = "",
        reversal_of: str | None = None,
    ) -> str:
        movement_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO inventory_movements(
                id, seller_sku, movement_type, quantity_delta,
                occurred_on, source_type, source_id, note, created_at,
                actor, reason, reversal_of
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                movement_id,
                seller_sku,
                movement_type,
                quantity_delta,
                occurred_on.isoformat(),
                source_type,
                source_id,
                note,
                now,
                actor,
                reason,
                reversal_of,
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
        return movement_id

    @staticmethod
    def _required(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise InventoryError(f"{label} is required")
        return normalized

    @staticmethod
    def _audit(
        connection,
        object_type: str,
        object_id: str,
        action: str,
        actor: str,
        reason: str | None,
        before: dict | None,
        after: dict | None,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                id, object_type, object_id, action, before_json,
                after_json, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                object_type,
                object_id,
                action,
                None if before is None else json.dumps(before, sort_keys=True),
                None if after is None else json.dumps(after, sort_keys=True),
                actor,
                reason,
                now,
            ),
        )

    def list_missing_sku_costs(self) -> tuple[dict, ...]:
        """查询待补 SKU 成本列表

        关联 platform_order_lines、sku_cost_history，
        找出有订单但无有效成本版本的 SKU
        返回: seller_sku, platform, store, order_count, last_order_date
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    pol.sku_identity AS seller_sku,
                    pol.platform,
                    pol.store,
                    COUNT(DISTINCT pol.order_id) AS order_count,
                    MIN(substr(pol.ordered_at, 1, 10)) AS first_missing_date,
                    MAX(substr(pol.ordered_at, 1, 10)) AS last_order_date,
                    SUM(pol.quantity) AS missing_units
                FROM effective_order_sku_lines AS pol
                WHERE pol.resolution_status = 'ready'
                  AND LOWER(TRIM(COALESCE(pol.order_status, '')))
                      NOT IN ('cancelled', 'canceled')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM sku_cost_history AS sch
                      WHERE sch.platform = pol.platform
                        AND sch.seller_sku = pol.sku_identity
                        AND sch.effective_date <= substr(pol.ordered_at, 1, 10)
                        AND (
                            sch.store = pol.store
                            OR EXISTS (
                                SELECT 1
                                FROM store_aliases AS alias
                                WHERE alias.platform = pol.platform
                                  AND (
                                      (
                                          alias.source_store = pol.store
                                          AND alias.canonical_store = sch.store
                                      )
                                      OR (
                                          alias.canonical_store = pol.store
                                          AND alias.source_store = sch.store
                                      )
                                  )
                            )
                        )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM order_cost_snapshots AS snapshot
                      LEFT JOIN store_aliases AS snapshot_alias
                        ON snapshot_alias.platform=snapshot.platform
                       AND snapshot_alias.source_store=snapshot.store
                      WHERE snapshot.platform=pol.platform
                        AND COALESCE(
                            snapshot_alias.canonical_store,
                            snapshot.store
                        )=pol.canonical_store
                        AND snapshot.order_id=pol.order_id
                        AND snapshot.seller_sku=pol.sku_identity
                        AND snapshot.status IN ('confirmed', 'returned')
                        AND snapshot.quantity>=pol.quantity
                  )
                GROUP BY pol.sku_identity, pol.platform, pol.store
                ORDER BY order_count DESC
                """
            ).fetchall()
            return tuple(
                {
                    "seller_sku": row["seller_sku"],
                    "platform": row["platform"],
                    "store": row["store"],
                    "order_count": row["order_count"],
                    "first_missing_date": row["first_missing_date"],
                    "last_order_date": row["last_order_date"],
                    "missing_units": row["missing_units"],
                }
                for row in rows
            )

    def platform_sku_count(self) -> int:
        """Return the number of distinct platform/store SKU facts.

        This deliberately reads the platform mapping fact table instead of the
        manual inventory ledger.  A newly synced store can therefore have a
        meaningful SKU count even before any manual stock movements exist.
        """
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS sku_count
                FROM (
                    SELECT platform, store, seller_sku
                    FROM platform_sku_mappings
                    WHERE TRIM(COALESCE(seller_sku, '')) <> ''
                    GROUP BY platform, store, seller_sku
                )
                """
            ).fetchone()
        return int(row["sku_count"] or 0)

    def latest_platform_inventory(self) -> dict:
        """Summarize the newest platform inventory snapshot.

        The summary is intentionally separate from ``inventory_balances``:
        platform availability is an observed fact, while the latter is the
        manually maintained stock ledger used for stocked fulfillment.
        """
        with self.database.connect() as connection:
            row = connection.execute(
                """
                WITH ranked AS (
                    SELECT
                        platform, store, seller_sku, snapshot_date,
                        available_units,
                        ROW_NUMBER() OVER (
                            PARTITION BY platform, store, seller_sku
                            ORDER BY snapshot_date DESC, observed_at DESC,
                                     item_id, model_id
                        ) AS row_number
                    FROM platform_inventory_snapshots
                    WHERE TRIM(COALESCE(seller_sku, '')) <> ''
                )
                SELECT
                    MAX(snapshot_date) AS snapshot_date,
                    COUNT(*) AS sku_count,
                    COALESCE(SUM(available_units), 0) AS available_units
                FROM ranked
                WHERE row_number=1
                """
            ).fetchone()
            if row["snapshot_date"] is None:
                return {
                    "snapshot_date": None,
                    "sku_count": 0,
                    "available_units": 0,
                }
        return {
            "snapshot_date": row["snapshot_date"],
            "sku_count": int(row["sku_count"] or 0),
            "available_units": int(row["available_units"] or 0),
        }

    def list_order_cost_gaps(self) -> tuple[dict, ...]:
        """Return order lines without an immutable cost snapshot.

        This is a different business gap from a missing SKU unit-price
        version: a SKU can have a price history and still lack the order-level
        fulfillment/cost snapshot needed for profit reconstruction.
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    substr(pol.ordered_at, 1, 10) AS order_date,
                    pol.platform,
                    pol.store,
                    pol.order_id,
                    pol.sku_identity AS seller_sku,
                    SUM(pol.quantity) - COALESCE(
                        (
                            SELECT SUM(ocs.quantity)
                            FROM order_cost_snapshots AS ocs
                            WHERE ocs.platform = pol.platform
                              AND ocs.order_id = pol.order_id
                              AND ocs.seller_sku = pol.sku_identity
                              AND ocs.status = 'confirmed'
                              AND (
                                  ocs.store = pol.store
                                  OR EXISTS (
                                      SELECT 1
                                      FROM store_aliases AS alias
                                      WHERE alias.platform = pol.platform
                                        AND (
                                            (
                                                alias.source_store = pol.store
                                                AND alias.canonical_store = ocs.store
                                            )
                                            OR (
                                                alias.canonical_store = pol.store
                                                AND alias.source_store = ocs.store
                                            )
                                        )
                                  )
                              )
                        ),
                        0
                    ) AS missing_units
                FROM effective_order_sku_lines AS pol
                WHERE pol.resolution_status = 'ready'
                  AND LOWER(TRIM(COALESCE(pol.order_status, '')))
                          NOT IN ('cancelled', 'canceled', 'returned',
                                  'refund_completed', 'refunded',
                                  'return_completed')
                  AND LOWER(TRIM(COALESCE(pol.refund_status, '')))
                          NOT IN ('returned', 'refund_completed', 'refunded',
                                  'return_completed')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM platform_order_lines AS historical
                      LEFT JOIN store_aliases AS historical_alias
                        ON historical_alias.platform=historical.platform
                       AND historical_alias.source_store=historical.store
                      WHERE historical.platform=pol.platform
                        AND COALESCE(
                            historical_alias.canonical_store,
                            historical.store
                        )=pol.canonical_store
                        AND historical.order_id=pol.order_id
                        AND historical.seller_sku=pol.raw_seller_sku
                        AND LOWER(TRIM(COALESCE(
                            historical.refund_status, ''
                        ))) IN ('returned', 'refund_completed', 'refunded',
                                'return_completed')
                  )
                GROUP BY
                    substr(pol.ordered_at, 1, 10), pol.platform, pol.store,
                    pol.order_id, pol.sku_identity
                HAVING SUM(pol.quantity) > COALESCE(
                    (
                        SELECT SUM(ocs.quantity)
                        FROM order_cost_snapshots AS ocs
                        WHERE ocs.platform = pol.platform
                          AND ocs.order_id = pol.order_id
                          AND ocs.seller_sku = pol.sku_identity
                          AND ocs.status = 'confirmed'
                          AND (
                              ocs.store = pol.store
                              OR EXISTS (
                                  SELECT 1
                                  FROM store_aliases AS alias
                                  WHERE alias.platform = pol.platform
                                    AND (
                                        (
                                            alias.source_store = pol.store
                                            AND alias.canonical_store = ocs.store
                                        )
                                        OR (
                                            alias.canonical_store = pol.store
                                            AND alias.source_store = ocs.store
                                        )
                                    )
                              )
                          )
                    ),
                    0
                )
                ORDER BY order_date, pol.store, pol.order_id, pol.seller_sku
                """
            ).fetchall()
        return tuple(
            {
                "order_date": row["order_date"],
                "platform": row["platform"],
                "store": row["store"],
                "order_id": row["order_id"],
                "seller_sku": row["seller_sku"],
                "missing_units": int(row["missing_units"] or 0),
            }
            for row in rows
        )

    def list_purchase_receipts(
        self,
        *,
        supplier: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[dict, ...]:
        """查询采购入库记录列表

        支持按供应商、日期范围筛选
        返回: id, supplier, received_on, status, line_count, total_amount_cny, created_at
        """
        query = """
            SELECT
                pr.id,
                pr.supplier,
                pr.received_on,
                pr.status,
                pr.created_by,
                pr.reversal_reason,
                COUNT(pl.seller_sku) AS line_count,
                COALESCE(SUM(CAST(pl.line_cost_cny AS REAL)), 0) AS total_amount_cny,
                pr.created_at
            FROM purchase_receipts AS pr
            LEFT JOIN purchase_lines AS pl ON pr.id = pl.receipt_id
            WHERE 1 = 1
        """
        params: list = []

        if supplier is not None:
            query += " AND pr.supplier LIKE ?"
            params.append(f"%{supplier}%")

        if start_date is not None:
            query += " AND pr.received_on >= ?"
            params.append(start_date.isoformat())

        if end_date is not None:
            query += " AND pr.received_on <= ?"
            params.append(end_date.isoformat())

        query += " GROUP BY pr.id ORDER BY pr.received_on DESC"

        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            evidence = self._evidence_by_object(
                connection, "purchase_receipt"
            )
        return tuple(
            {
                "id": row["id"],
                "supplier": row["supplier"],
                "received_on": row["received_on"],
                "status": row["status"],
                "created_by": row["created_by"],
                "reversal_reason": row["reversal_reason"],
                "line_count": row["line_count"],
                "total_amount_cny": row["total_amount_cny"],
                "created_at": row["created_at"],
                "evidence_ids": evidence.get(str(row["id"]), ()),
            }
            for row in rows
        )

    def list_inventory_movements(
        self,
        *,
        seller_sku: str | None = None,
        movement_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[dict, ...]:
        """查询库存流水

        支持按 SKU、流水类型、日期范围筛选
        返回: id, seller_sku, movement_type, quantity_delta, occurred_on, source_type, source_id
        """
        query = """
            SELECT
                id,
                seller_sku,
                movement_type,
                quantity_delta,
                occurred_on,
                source_type,
                source_id,
                note,
                actor,
                reason,
                reversal_of,
                EXISTS(
                    SELECT 1 FROM inventory_movements AS reversal
                    WHERE reversal.reversal_of=inventory_movements.id
                ) AS is_reversed
            FROM inventory_movements
            WHERE 1 = 1
        """
        params: list = []

        if seller_sku is not None:
            query += " AND seller_sku = ?"
            params.append(seller_sku)

        if movement_type is not None:
            query += " AND movement_type = ?"
            params.append(movement_type)

        if start_date is not None:
            query += " AND occurred_on >= ?"
            params.append(start_date.isoformat())

        if end_date is not None:
            query += " AND occurred_on <= ?"
            params.append(end_date.isoformat())

        query += " ORDER BY occurred_on DESC LIMIT 500"

        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            evidence = self._evidence_by_object(
                connection, "inventory_movement"
            )
        return tuple(
            {
                "id": row["id"],
                "seller_sku": row["seller_sku"],
                "movement_type": row["movement_type"],
                "quantity_delta": row["quantity_delta"],
                "occurred_on": row["occurred_on"],
                "source_type": row["source_type"],
                "source_id": row["source_id"],
                "note": row["note"],
                "actor": row["actor"],
                "reason": row["reason"],
                "reversal_of": row["reversal_of"],
                "is_reversed": bool(row["is_reversed"]),
                "evidence_ids": evidence.get(str(row["id"]), ()),
            }
            for row in rows
        )

    @staticmethod
    def _evidence_by_object(connection, object_type: str) -> dict[str, tuple[str, ...]]:
        rows = connection.execute(
            """
            SELECT object_id, id FROM evidence_documents
            WHERE object_type=? ORDER BY created_at, id
            """,
            (object_type,),
        ).fetchall()
        collected: dict[str, list[str]] = {}
        for row in rows:
            collected.setdefault(str(row["object_id"]), []).append(str(row["id"]))
        return {key: tuple(value) for key, value in collected.items()}

    def list_inventory_balances(self) -> tuple[dict, ...]:
        """查询所有 SKU 的当前库存余额

        返回: seller_sku, units, updated_at
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    seller_sku,
                    units,
                    updated_at
                FROM inventory_balances
                ORDER BY units ASC
                """
            ).fetchall()
            return tuple(
                {
                    "seller_sku": row["seller_sku"],
                    "units": row["units"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            )

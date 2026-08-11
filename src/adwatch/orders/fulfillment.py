from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from adwatch.storage.db import Database
from adwatch.storage.store_aliases import equivalent_store_names

MODES = frozenset({"supplier_fulfilled", "stocked"})
SUPPLY_STATUSES = frozenset({"available", "paused"})


@dataclass(frozen=True)
class FulfillmentSnapshot:
    platform: str
    store: str
    order_id: str
    seller_sku: str
    mode: str
    policy_effective_date: date
    supply_status: str
    resolution_source: str


class FulfillmentService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def set_policy(
        self,
        *,
        platform: str,
        store: str,
        seller_sku: str,
        effective_date: date,
        mode: str,
        supply_status: str,
        note: str = "",
    ) -> None:
        if mode not in MODES:
            raise ValueError("invalid fulfillment mode")
        if supply_status not in SUPPLY_STATUSES:
            raise ValueError("invalid supply status")
        if not all((platform.strip(), store.strip(), seller_sku.strip())):
            raise ValueError("platform, store and seller SKU are required")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sku_fulfillment_history(
                    platform, store, seller_sku, effective_date,
                    mode, supply_status, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(platform, store, seller_sku, effective_date)
                DO UPDATE SET mode=excluded.mode,
                              supply_status=excluded.supply_status,
                              note=excluded.note
                """,
                (
                    platform.lower(),
                    store,
                    seller_sku,
                    effective_date.isoformat(),
                    mode,
                    supply_status,
                    note,
                ),
            )

    def set_store_default(
        self,
        *,
        platform: str,
        store: str,
        effective_date: date,
        mode: str,
        supply_status: str,
        note: str = "",
    ) -> None:
        if mode not in MODES:
            raise ValueError("invalid fulfillment mode")
        if supply_status not in SUPPLY_STATUSES:
            raise ValueError("invalid supply status")
        if not all((platform.strip(), store.strip())):
            raise ValueError("platform and store are required")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO store_fulfillment_history(
                    platform, store, effective_date, mode,
                    supply_status, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, store, effective_date)
                DO UPDATE SET mode=excluded.mode,
                              supply_status=excluded.supply_status,
                              note=excluded.note
                """,
                (
                    platform.lower(),
                    store,
                    effective_date.isoformat(),
                    mode,
                    supply_status,
                    note,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def resolve_for_order(
        self,
        *,
        platform: str,
        store: str,
        seller_sku: str,
        ordered_on: date,
    ):
        with self.database.connect() as connection:
            stores = equivalent_store_names(
                connection, platform=platform, store=store
            )
            placeholders = ", ".join("?" for _ in stores)
            policy = connection.execute(
                f"""
                SELECT mode, supply_status, effective_date
                FROM sku_fulfillment_history
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
                    ordered_on.isoformat(),
                    store,
                ),
            ).fetchone()
            if policy is not None:
                return policy
            return connection.execute(
                f"""
                SELECT mode, supply_status, effective_date
                FROM store_fulfillment_history
                WHERE platform=? AND store IN ({placeholders})
                  AND effective_date<=?
                ORDER BY CASE WHEN store=? THEN 0 ELSE 1 END,
                         effective_date DESC, store
                LIMIT 1
                """,
                (
                    platform.lower(),
                    *stores,
                    ordered_on.isoformat(),
                    store,
                ),
            ).fetchone()

    def snapshot_order(
        self,
        *,
        platform: str,
        store: str,
        order_id: str,
        seller_sku: str,
        ordered_on: date,
    ) -> FulfillmentSnapshot | None:
        with self.database.transaction() as connection:
            stores = equivalent_store_names(
                connection, platform=platform, store=store
            )
            placeholders = ", ".join("?" for _ in stores)
            existing = connection.execute(
                f"""
                SELECT * FROM order_fulfillment_snapshots
                WHERE platform=? AND store IN ({placeholders})
                  AND order_id=? AND seller_sku=?
                ORDER BY CASE WHEN store=? THEN 0 ELSE 1 END, store
                LIMIT 1
                """,
                (platform.lower(), *stores, order_id, seller_sku, store),
            ).fetchone()
            if existing is not None:
                return self._snapshot(existing)
            policy = connection.execute(
                f"""
                SELECT mode, supply_status, effective_date
                FROM sku_fulfillment_history
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
                    ordered_on.isoformat(),
                    store,
                ),
            ).fetchone()
            resolution_source = "sku_policy"
            if policy is None:
                default_placeholders = ", ".join("?" for _ in stores)
                policy = connection.execute(
                    f"""
                    SELECT mode, supply_status, effective_date
                    FROM store_fulfillment_history
                    WHERE platform=? AND store IN ({default_placeholders})
                      AND effective_date<=?
                    ORDER BY CASE WHEN store=? THEN 0 ELSE 1 END,
                             effective_date DESC, store
                    LIMIT 1
                    """,
                    (
                        platform.lower(),
                        *stores,
                        ordered_on.isoformat(),
                        store,
                    ),
                ).fetchone()
                resolution_source = "store_default"
            if policy is None:
                return None
            connection.execute(
                """
                INSERT INTO order_fulfillment_snapshots(
                    platform, store, order_id, seller_sku, mode,
                    policy_effective_date, supply_status,
                    resolution_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform.lower(),
                    store,
                    order_id,
                    seller_sku,
                    policy["mode"],
                    policy["effective_date"],
                    policy["supply_status"],
                    resolution_source,
                    datetime.now(UTC).isoformat(),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM order_fulfillment_snapshots
                WHERE platform=? AND store=? AND order_id=? AND seller_sku=?
                """,
                (platform.lower(), store, order_id, seller_sku),
            ).fetchone()
            return self._snapshot(row)

    def mark_current_skus_supplier_fulfilled(
        self, *, platform: str, store: str, note: str = ""
    ) -> int:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT seller_sku, MIN(effective_date) AS effective_date
                FROM sku_cost_history
                WHERE platform=? AND store=?
                GROUP BY seller_sku
                """,
                (platform.lower(), store),
            ).fetchall()
            count = 0
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO sku_fulfillment_history(
                        platform, store, seller_sku, effective_date,
                        mode, supply_status, note, created_at
                    ) VALUES(
                        ?, ?, ?, ?, 'supplier_fulfilled',
                        'available', ?, ?
                    )
                    """,
                    (
                        platform.lower(),
                        store,
                        row["seller_sku"],
                        row["effective_date"],
                        note,
                        now,
                    ),
                )
                count += cursor.rowcount
        return count

    @staticmethod
    def _snapshot(row) -> FulfillmentSnapshot:
        return FulfillmentSnapshot(
            platform=row["platform"],
            store=row["store"],
            order_id=row["order_id"],
            seller_sku=row["seller_sku"],
            mode=row["mode"],
            policy_effective_date=date.fromisoformat(
                row["policy_effective_date"]
            ),
            supply_status=row["supply_status"],
            resolution_source=row["resolution_source"],
        )

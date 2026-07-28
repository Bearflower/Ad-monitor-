from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from adwatch.storage.db import Database

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

    def resolve_for_order(
        self,
        *,
        platform: str,
        store: str,
        seller_sku: str,
        ordered_on: date,
    ):
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT mode, supply_status, effective_date
                FROM sku_fulfillment_history
                WHERE platform=? AND store=? AND seller_sku=?
                  AND effective_date<=?
                ORDER BY effective_date DESC LIMIT 1
                """,
                (
                    platform.lower(),
                    store,
                    seller_sku,
                    ordered_on.isoformat(),
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
            existing = connection.execute(
                """
                SELECT * FROM order_fulfillment_snapshots
                WHERE platform=? AND store=? AND order_id=? AND seller_sku=?
                """,
                (platform.lower(), store, order_id, seller_sku),
            ).fetchone()
            if existing is not None:
                return self._snapshot(existing)
            policy = connection.execute(
                """
                SELECT mode, supply_status, effective_date
                FROM sku_fulfillment_history
                WHERE platform=? AND store=? AND seller_sku=?
                  AND effective_date<=?
                ORDER BY effective_date DESC LIMIT 1
                """,
                (
                    platform.lower(),
                    store,
                    seller_sku,
                    ordered_on.isoformat(),
                ),
            ).fetchone()
            if policy is None:
                return None
            connection.execute(
                """
                INSERT INTO order_fulfillment_snapshots(
                    platform, store, order_id, seller_sku, mode,
                    policy_effective_date, supply_status,
                    resolution_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'sku_policy', ?)
                """,
                (
                    platform.lower(),
                    store,
                    order_id,
                    seller_sku,
                    policy["mode"],
                    policy["effective_date"],
                    policy["supply_status"],
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
        )

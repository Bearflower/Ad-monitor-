from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from adwatch.storage.db import Database


@dataclass(frozen=True)
class PlatformInventorySnapshot:
    platform: str
    store: str
    item_id: str
    model_id: str
    seller_sku: str
    snapshot_date: date
    available_units: int
    observed_at: datetime
    source: str


class InventoryFactRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_snapshots(
        self,
        records: tuple[PlatformInventorySnapshot, ...],
    ) -> int:
        changed = 0
        with self.database.transaction() as connection:
            for record in records:
                key = (
                    record.platform,
                    record.store,
                    record.item_id,
                    record.model_id,
                    record.snapshot_date.isoformat(),
                )
                business_fact = (
                    record.seller_sku,
                    record.available_units,
                    record.source,
                )
                fact = (
                    *business_fact[:-1],
                    record.observed_at.isoformat(),
                    business_fact[-1],
                )
                existing = connection.execute(
                    """
                    SELECT seller_sku, available_units, source
                    FROM platform_inventory_snapshots
                    WHERE platform=? AND store=? AND item_id=?
                      AND model_id=? AND snapshot_date=?
                    """,
                    key,
                ).fetchone()
                if (
                    existing is not None
                    and tuple(existing) == business_fact
                ):
                    continue
                connection.execute(
                    """
                    INSERT INTO platform_inventory_snapshots(
                        platform, store, item_id, model_id, snapshot_date,
                        seller_sku, available_units, observed_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        platform, store, item_id, model_id, snapshot_date
                    ) DO UPDATE SET
                        seller_sku=excluded.seller_sku,
                        available_units=excluded.available_units,
                        observed_at=excluded.observed_at,
                        source=excluded.source
                    """,
                    (*key, *fact),
                )
                changed += 1
        return changed


@dataclass(frozen=True)
class StaticInventorySource:
    values: dict[tuple[str, date], tuple[int, Decimal]]

    def fetch(
        self, sku_id: str, data_date: date
    ) -> tuple[int, Decimal] | None:
        return self.values.get((sku_id, data_date))

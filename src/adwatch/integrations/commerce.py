from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Generic, TypeVar

from adwatch.storage.db import Database

T = TypeVar("T")


class CapabilityStatus(str, Enum):
    READY = "ready"
    PENDING_DATA = "pending_data"
    PENDING_EXTERNAL = "pending_external"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CapabilityResult(Generic[T]):
    status: CapabilityStatus
    records: tuple[T, ...]
    reason: str = ""

    @classmethod
    def ready(cls, records: tuple[T, ...]) -> CapabilityResult[T]:
        return cls(CapabilityStatus.READY, records)

    @classmethod
    def pending_external(cls, reason: str) -> CapabilityResult[T]:
        return cls(CapabilityStatus.PENDING_EXTERNAL, (), reason)


@dataclass(frozen=True)
class SettlementRecord:
    external_key: str
    platform: str
    store: str
    order_id: str
    settled_on: date
    amount_original: Decimal
    currency: str
    rate_to_cny: Decimal
    source: str


class CommerceRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_settlements(
        self, records: tuple[SettlementRecord, ...]
    ) -> int:
        inserted = 0
        with self.database.transaction() as connection:
            for record in records:
                existing = connection.execute(
                    "SELECT * FROM settlement_records WHERE external_key=?",
                    (record.external_key,),
                ).fetchone()
                amount_cny = (
                    record.amount_original * record.rate_to_cny
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                fact = (
                    record.platform,
                    record.store,
                    record.order_id,
                    record.settled_on.isoformat(),
                    str(record.amount_original),
                    record.currency,
                    str(record.rate_to_cny),
                    str(amount_cny),
                    record.source,
                )
                if existing:
                    stored = tuple(
                        existing[key]
                        for key in (
                            "platform",
                            "store",
                            "order_id",
                            "settled_on",
                            "amount_original",
                            "currency",
                            "rate_to_cny",
                            "amount_cny",
                            "source",
                        )
                    )
                    if stored != fact:
                        raise ValueError("platform settlement facts are immutable")
                    continue
                connection.execute(
                    """
                    INSERT INTO settlement_records(
                        external_key, platform, store, order_id, settled_on,
                        amount_original, currency, rate_to_cny, amount_cny,
                        source, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.external_key,
                        *fact,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                inserted += 1
        return inserted

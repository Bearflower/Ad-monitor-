from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeVar

from adwatch.storage.db import Database

if TYPE_CHECKING:
    from adwatch.income.models import FeeStatement

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


@dataclass(frozen=True)
class AdFundingRecord:
    external_key: str
    platform: str
    store: str
    occurred_on: date
    entry_type: str
    amount_original: Decimal
    currency: str
    rate_to_cny: Decimal
    source: str


@dataclass(frozen=True)
class AdSpendRecord:
    external_key: str
    platform: str
    store: str
    campaign_id: str
    occurred_on: date
    amount_original: Decimal
    currency: str
    rate_to_cny: Decimal
    source: str


@dataclass(frozen=True)
class RefundRecord:
    external_key: str
    platform: str
    store: str
    order_id: str
    occurred_on: date
    amount_original: Decimal
    currency: str
    status: str


@dataclass(frozen=True)
class LogisticsRecord:
    external_key: str
    platform: str
    store: str
    order_id: str
    occurred_on: date
    logistics_status: str
    tracking_number: str


@dataclass(frozen=True)
class RefundObservation:
    platform: str
    store: str
    order_id: str
    observed_on: date
    order_date: date
    refund_status: str
    amount_original: Decimal | None
    currency: str
    source_updated_at: datetime
    observed_at: datetime
    source: str


@dataclass(frozen=True)
class LogisticsObservation:
    platform: str
    store: str
    order_id: str
    observed_on: date
    order_date: date
    logistics_status: str
    source_updated_at: datetime
    observed_at: datetime
    source: str


@dataclass(frozen=True)
class PlatformFeeRecord:
    external_key: str
    platform: str
    store: str
    order_id: str
    occurred_on: date
    fee_type: str
    amount_original: Decimal
    currency: str
    rate_to_cny: Decimal


@dataclass(frozen=True)
class IncomeFactCounts:
    settlement_count: int = 0
    fee_count: int = 0
    funding_count: int = 0


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

    def upsert_income_statement_facts(
        self,
        connection,
        statement: FeeStatement,
    ) -> IncomeFactCounts:
        observed_at = datetime.now(UTC).isoformat()
        prefix = f"income:{statement.source_sha256}"
        stored_statement = connection.execute(
            """
            SELECT id, version FROM platform_fee_statements
            WHERE source_sha256=?
            """,
            (statement.source_sha256,),
        ).fetchone()
        if stored_statement is None:
            raise ValueError("income statement must be stored before its facts")
        statement_id = str(stored_statement["id"])
        statement_version = int(stored_statement["version"])
        fact_tables = (
            "platform_settlement_summaries",
            "platform_fee_entries",
            "platform_ad_funding_observations",
        )
        for table in fact_tables:
            connection.execute(
                f"""
                UPDATE {table} SET is_current=0
                WHERE statement_id<>? AND statement_id IN (
                    SELECT id FROM platform_fee_statements
                    WHERE platform=? AND store=?
                      AND period_start=? AND period_end=?
                )
                """,
                (
                    statement_id,
                    statement.platform,
                    statement.store,
                    statement.period_start.isoformat(),
                    statement.period_end.isoformat(),
                ),
            )
        settlement = connection.execute(
            """
            INSERT OR IGNORE INTO platform_settlement_summaries(
                external_key, platform, store, period_start, period_end,
                gross_sales_original, payout_original, currency,
                recognition_status, source_sha256, source, observed_at,
                statement_id, statement_version, is_current
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, 'settlement_only', ?, ?, ?, ?, ?, 1
            )
            """,
            (
                f"{prefix}:settlement",
                statement.platform,
                statement.store,
                statement.period_start.isoformat(),
                statement.period_end.isoformat(),
                str(statement.product_price),
                str(statement.total_payout),
                statement.currency,
                statement.source_sha256,
                "shopee_income_pdf",
                observed_at,
                statement_id,
                statement_version,
            ),
        ).rowcount
        fee_values = (
            ("commission_fee", -statement.commission_fee),
            ("service_fee", -statement.service_fee),
            ("transaction_fee", -statement.transaction_fee),
            ("affiliate_fee", -statement.affiliate_fee),
            ("seller_voucher", -statement.seller_voucher),
            ("shipping_paid_by_buyer", statement.shipping_paid_by_buyer),
            (
                "shipping_provider_charge",
                -statement.shipping_provider_charge,
            ),
            ("shipping_rebate", statement.shipping_rebate),
            ("adjustment_amount", statement.adjustment_amount),
        )
        fee_count = 0
        for fee_type, amount in fee_values:
            fee_count += connection.execute(
                """
                INSERT OR IGNORE INTO platform_fee_entries(
                    external_key, platform, store, period_start, period_end,
                    fee_type, amount_original, currency, source_sha256,
                    source, observed_at, statement_id, statement_version,
                    is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    f"{prefix}:fee:{fee_type}",
                    statement.platform,
                    statement.store,
                    statement.period_start.isoformat(),
                    statement.period_end.isoformat(),
                    fee_type,
                    str(amount),
                    statement.currency,
                    statement.source_sha256,
                    "shopee_income_pdf",
                    observed_at,
                    statement_id,
                    statement_version,
                ),
            ).rowcount
        funding = connection.execute(
            """
            INSERT OR IGNORE INTO platform_ad_funding_observations(
                external_key, platform, store, occurred_on, entry_type,
                amount_original, currency, funding_source, source_sha256,
                source, observed_at, statement_id, statement_version,
                is_current
            ) VALUES (?, ?, ?, ?, 'topup', ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                f"{prefix}:ad_funding",
                statement.platform,
                statement.store,
                statement.period_end.isoformat(),
                str(statement.ads_credit_topup),
                statement.currency,
                "settlement_deduction",
                statement.source_sha256,
                "shopee_income_pdf",
                observed_at,
                statement_id,
                statement_version,
            ),
        ).rowcount
        return IncomeFactCounts(
            settlement_count=settlement,
            fee_count=fee_count,
            funding_count=funding,
        )

    def upsert_refund_observations(
        self,
        records: tuple[RefundObservation, ...],
    ) -> int:
        changed = 0
        with self.database.transaction() as connection:
            for record in records:
                key = (
                    record.platform,
                    record.store,
                    record.order_id,
                    record.observed_on.isoformat(),
                )
                business_fact = (
                    record.order_date.isoformat(),
                    record.refund_status,
                    (
                        None
                        if record.amount_original is None
                        else str(record.amount_original)
                    ),
                    record.currency,
                    record.source,
                )
                fact = (
                    *business_fact[:-1],
                    record.source_updated_at.isoformat(),
                    record.observed_at.isoformat(),
                    business_fact[-1],
                )
                existing = connection.execute(
                    """
                    SELECT order_date, refund_status, amount_original,
                           currency, source
                    FROM platform_refund_observations
                    WHERE platform=? AND store=? AND order_id=?
                      AND observed_on=?
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
                    INSERT INTO platform_refund_observations(
                        platform, store, order_id, observed_on, order_date,
                        refund_status, amount_original, currency,
                        source_updated_at, observed_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, store, order_id, observed_on)
                    DO UPDATE SET
                        order_date=excluded.order_date,
                        refund_status=excluded.refund_status,
                        amount_original=excluded.amount_original,
                        currency=excluded.currency,
                        source_updated_at=excluded.source_updated_at,
                        observed_at=excluded.observed_at,
                        source=excluded.source
                    """,
                    (*key, *fact),
                )
                changed += 1
        return changed

    def upsert_logistics_observations(
        self,
        records: tuple[LogisticsObservation, ...],
    ) -> int:
        changed = 0
        with self.database.transaction() as connection:
            for record in records:
                key = (
                    record.platform,
                    record.store,
                    record.order_id,
                    record.observed_on.isoformat(),
                )
                business_fact = (
                    record.order_date.isoformat(),
                    record.logistics_status,
                    record.source,
                )
                fact = (
                    *business_fact[:-1],
                    record.source_updated_at.isoformat(),
                    record.observed_at.isoformat(),
                    business_fact[-1],
                )
                existing = connection.execute(
                    """
                    SELECT order_date, logistics_status, source
                    FROM platform_logistics_observations
                    WHERE platform=? AND store=? AND order_id=?
                      AND observed_on=?
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
                    INSERT INTO platform_logistics_observations(
                        platform, store, order_id, observed_on, order_date,
                        logistics_status, source_updated_at, observed_at,
                        source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, store, order_id, observed_on)
                    DO UPDATE SET
                        order_date=excluded.order_date,
                        logistics_status=excluded.logistics_status,
                        source_updated_at=excluded.source_updated_at,
                        observed_at=excluded.observed_at,
                        source=excluded.source
                    """,
                    (*key, *fact),
                )
                changed += 1
        return changed

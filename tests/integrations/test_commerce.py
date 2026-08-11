from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from adwatch.income.models import FeeStatement
from adwatch.income.repository import FeeStatementRepository
from adwatch.integrations.commerce import (
    AdFundingRecord,
    AdSpendRecord,
    CapabilityResult,
    CapabilityStatus,
    CommerceRepository,
    LogisticsObservation,
    LogisticsRecord,
    PlatformFeeRecord,
    RefundObservation,
    RefundRecord,
    SettlementRecord,
)
from adwatch.storage.db import Database


def _income_statement(**changes) -> FeeStatement:
    statement = FeeStatement(
        platform="shopee",
        store="shop",
        source_store="store-alias",
        period_start=date(2026, 7, 20),
        period_end=date(2026, 7, 26),
        currency="THB",
        product_price=Decimal(4473),
        commission_fee=Decimal(500),
        service_fee=Decimal(379),
        transaction_fee=Decimal(150),
        affiliate_fee=Decimal(32),
        sales_fee_total=Decimal(1061),
        effective_rate=Decimal("0.237201"),
        seller_voucher=Decimal(35),
        shipping_paid_by_buyer=Decimal(147),
        shipping_provider_charge=Decimal(965),
        shipping_rebate=Decimal(818),
        ads_credit_topup=Decimal(13),
        adjustment_amount=Decimal(0),
        total_payout=Decimal(3364),
        source_name="weekly_report.pdf",
        source_sha256="a" * 64,
    )
    if "sales_fee_total" in changes:
        changes.setdefault(
            "effective_rate",
            (changes["sales_fee_total"] / statement.product_price).quantize(
                Decimal("0.000001")
            ),
        )
    return replace(statement, **changes)


def test_capability_result_distinguishes_no_data_from_external_blocker():
    assert CapabilityResult.ready(()).status is CapabilityStatus.READY
    pending = CapabilityResult.pending_external("平台未开放接口")
    assert pending.status is CapabilityStatus.PENDING_EXTERNAL
    assert pending.reason == "平台未开放接口"


def test_commerce_contracts_keep_recharge_spend_refund_logistics_and_fee_distinct():
    assert AdFundingRecord.__name__ != AdSpendRecord.__name__
    assert {
        RefundRecord.__name__,
        LogisticsRecord.__name__,
        PlatformFeeRecord.__name__,
    } == {"RefundRecord", "LogisticsRecord", "PlatformFeeRecord"}


def test_settlement_import_is_idempotent_and_preserves_platform_fact(tmp_path):
    database = Database(tmp_path / "commerce.sqlite3")
    database.migrate()
    repository = CommerceRepository(database)
    record = SettlementRecord(
        external_key="shopee:shop:ORDER-1",
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        settled_on=date(2026, 7, 28),
        amount_original=Decimal("120.50"),
        currency="THB",
        rate_to_cny=Decimal("0.21"),
        source="ziniao_cli",
    )

    assert repository.upsert_settlements((record,)) == 1
    assert repository.upsert_settlements((record,)) == 0
    with pytest.raises(ValueError, match="immutable"):
        repository.upsert_settlements(
            (record.__class__(**{**record.__dict__, "amount_original": Decimal(1)}),)
        )


def test_refund_observations_are_idempotent_and_preserve_daily_history(
    tmp_path,
):
    database = Database(tmp_path / "commerce.sqlite3")
    database.migrate()
    repository = CommerceRepository(database)
    observed = datetime(2026, 8, 4, 9, tzinfo=UTC)
    record = RefundObservation(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        observed_on=date(2026, 8, 4),
        order_date=date(2026, 8, 1),
        refund_status="returned",
        amount_original=None,
        currency="THB",
        source_updated_at=observed,
        observed_at=observed,
        source="shopee_order_card",
    )

    assert repository.upsert_refund_observations((record,)) == 1
    later_observation = record.__class__(
        **{
            **record.__dict__,
            "source_updated_at": datetime(2026, 8, 4, 10, tzinfo=UTC),
            "observed_at": datetime(2026, 8, 4, 10, tzinfo=UTC),
        }
    )
    assert repository.upsert_refund_observations((later_observation,)) == 0
    corrected = record.__class__(
        **{**record.__dict__, "refund_status": "refund_completed"}
    )
    assert repository.upsert_refund_observations((corrected,)) == 1
    next_day = record.__class__(
        **{
            **corrected.__dict__,
            "observed_on": date(2026, 8, 5),
            "observed_at": datetime(2026, 8, 5, 9, tzinfo=UTC),
        }
    )
    assert repository.upsert_refund_observations((next_day,)) == 1

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT observed_on, refund_status, amount_original
            FROM platform_refund_observations
            ORDER BY observed_on
            """
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("2026-08-04", "refund_completed", None),
        ("2026-08-05", "refund_completed", None),
    ]


def test_logistics_observations_are_idempotent_per_day(tmp_path):
    database = Database(tmp_path / "commerce.sqlite3")
    database.migrate()
    repository = CommerceRepository(database)
    observed = datetime(2026, 8, 4, 9, tzinfo=UTC)
    record = LogisticsObservation(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        observed_on=date(2026, 8, 4),
        order_date=date(2026, 8, 1),
        logistics_status="delivered",
        source_updated_at=observed,
        observed_at=observed,
        source="shopee_order_card",
    )

    assert repository.upsert_logistics_observations((record,)) == 1
    later_observation = record.__class__(
        **{
            **record.__dict__,
            "source_updated_at": datetime(2026, 8, 4, 10, tzinfo=UTC),
            "observed_at": datetime(2026, 8, 4, 10, tzinfo=UTC),
        }
    )
    assert repository.upsert_logistics_observations((later_observation,)) == 0


def test_income_statement_facts_are_separate_and_idempotent(tmp_path):
    database = Database(tmp_path / "commerce.sqlite3")
    database.migrate()
    repository = CommerceRepository(database)
    statement = _income_statement()

    with database.transaction() as connection:
        FeeStatementRepository(database).save_with_connection(
            connection,
            statement,
        )
        first = repository.upsert_income_statement_facts(
            connection,
            statement,
        )
        second = repository.upsert_income_statement_facts(
            connection,
            statement,
        )

    assert (
        first.settlement_count,
        first.fee_count,
        first.funding_count,
    ) == (1, 9, 1)
    assert (
        second.settlement_count,
        second.fee_count,
        second.funding_count,
    ) == (0, 0, 0)


def test_income_revision_marks_old_normalized_facts_non_current(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    statements = FeeStatementRepository(database)
    commerce = CommerceRepository(database)
    original = _income_statement(source_sha256="a" * 64)
    revised = _income_statement(
        source_sha256="b" * 64,
        commission_fee=Decimal(600),
        sales_fee_total=Decimal(1161),
        total_payout=Decimal(3264),
    )

    with database.transaction() as connection:
        assert statements.save_with_connection(connection, original) == "imported"
        commerce.upsert_income_statement_facts(connection, original)
    with database.transaction() as connection:
        assert statements.save_with_connection(connection, revised) == "revised"
        commerce.upsert_income_statement_facts(connection, revised)

    with database.connect() as connection:
        counts = {
            table: tuple(
                connection.execute(
                    f"SELECT COUNT(*), SUM(is_current), "
                    f"COUNT(DISTINCT statement_id) FROM {table}"
                ).fetchone()
            )
            for table in (
                "platform_settlement_summaries",
                "platform_fee_entries",
                "platform_ad_funding_observations",
            )
        }
    assert counts == {
        "platform_settlement_summaries": (2, 1, 2),
        "platform_fee_entries": (18, 9, 2),
        "platform_ad_funding_observations": (2, 1, 2),
    }

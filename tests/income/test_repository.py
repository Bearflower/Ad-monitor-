from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from adwatch.income.models import FeeStatement
from adwatch.income.repository import FeeStatementRepository
from adwatch.storage.db import Database


def _statement(*, source_sha256: str = "a" * 64) -> FeeStatement:
    return FeeStatement(
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
        source_sha256=source_sha256,
    )


def test_fee_rate_prefers_covering_period_then_carries_forward(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    repository = FeeStatementRepository(database)

    assert repository.save(_statement()) == "imported"

    covering = repository.resolve("shopee", "shop", date(2026, 7, 22))
    carried = repository.resolve("shopee", "shop", date(2026, 7, 29))
    other_store = repository.resolve("shopee", "other", date(2026, 7, 22))

    assert covering is not None
    assert covering.rate == Decimal("0.237201")
    assert covering.selection == "covering_period"
    assert carried is not None
    assert carried.selection == "carried_forward"
    assert other_store is None


def test_same_sha_skips_and_same_period_revision_preserves_history(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    repository = FeeStatementRepository(database)
    original = _statement()

    assert repository.save(original) == "imported"
    assert repository.save(original) == "skipped"
    revised_total = Decimal(1161)
    revised = replace(
        original,
        source_sha256="b" * 64,
        source_name="weekly_report-revised.pdf",
        commission_fee=Decimal(600),
        sales_fee_total=revised_total,
        effective_rate=(
            revised_total / original.product_price
        ).quantize(Decimal("0.000001")),
        total_payout=Decimal(3264),
    )

    assert repository.save(revised) == "revised"

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT version, is_current, source_sha256
            FROM platform_fee_statements ORDER BY version
            """
        ).fetchall()
        events = connection.execute(
            """
            SELECT status, source_sha256
            FROM income_import_events ORDER BY occurred_at, source_sha256
            """
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (1, 0, "a" * 64),
        (2, 1, "b" * 64),
    ]
    assert {tuple(row) for row in events} == {
        ("imported", "a" * 64),
        ("revised", "b" * 64),
    }


def test_income_source_name_rejects_control_characters():
    with pytest.raises(ValueError, match="control"):
        replace(_statement(), source_name="weekly_report.pdf\nforged")


def test_terminal_event_cannot_overwrite_successful_import_audit(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    repository = FeeStatementRepository(database)
    statement = _statement()
    assert repository.save(statement) == "imported"

    repository.record_event(
        source_sha256=statement.source_sha256,
        source_name=statement.source_name,
        platform=statement.platform,
        store="",
        status="failed",
        reason_code="late_parser_failure",
    )

    with database.connect() as connection:
        event = connection.execute(
            """
            SELECT status, reason_code, statement_id
            FROM income_import_events WHERE source_sha256=?
            """,
            (statement.source_sha256,),
        ).fetchone()
    assert event["status"] == "imported"
    assert event["reason_code"] == ""
    assert event["statement_id"] is not None

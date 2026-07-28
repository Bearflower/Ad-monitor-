from datetime import date
from decimal import Decimal

import pytest

from adwatch.profit_sharing.service import ProfitSharingError, ProfitSharingService
from adwatch.storage.db import Database


@pytest.fixture
def sharing(tmp_path):
    database = Database(tmp_path / "sharing.sqlite3")
    database.migrate()
    return ProfitSharingService(database), database


def test_new_agreement_does_not_change_historical_period(sharing):
    service, database = sharing
    old = service.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal("0.60"), "苏姐": Decimal("0.40")},
        actor="yl",
    )
    period = service.create_period(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        net_profit_cny=Decimal(1000),
        actor="yl",
    )
    service.create_agreement(
        effective_from=date(2026, 8, 1),
        shares={"洁云": Decimal("0.50"), "苏姐": Decimal("0.50")},
        actor="yl",
    )

    with database.connect() as connection:
        stored = connection.execute(
            "SELECT agreement_id FROM profit_periods WHERE id=?", (period,)
        ).fetchone()
        allocations = connection.execute(
            """
            SELECT partner, amount_cny FROM profit_allocations
            WHERE period_id=? ORDER BY partner
            """,
            (period,),
        ).fetchall()

    assert stored["agreement_id"] == old
    assert {row["partner"]: row["amount_cny"] for row in allocations} == {
        "洁云": "600.00",
        "苏姐": "400.00",
    }


def test_agreement_shares_must_total_one(sharing):
    service, _ = sharing
    with pytest.raises(ProfitSharingError, match="total"):
        service.create_agreement(
            effective_from=date(2026, 1, 1),
            shares={"洁云": Decimal("0.60"), "苏姐": Decimal("0.50")},
            actor="yl",
        )


def test_confirm_period_and_record_partial_payment(sharing):
    service, database = sharing
    service.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal("0.60"), "苏姐": Decimal("0.40")},
        actor="yl",
    )
    period = service.create_period(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        net_profit_cny=Decimal(1000),
        actor="yl",
    )
    service.confirm_period(period, actor="yl")
    payment = service.record_payment(
        period_id=period,
        partner="洁云",
        amount_cny=Decimal(300),
        paid_on=date(2026, 8, 1),
        status="paid",
        note="首笔",
        actor="yl",
    )
    assert payment
    with database.connect() as connection:
        row = connection.execute(
            "SELECT amount_cny, status FROM profit_payments WHERE id=?",
            (payment,),
        ).fetchone()
    assert dict(row) == {"amount_cny": "300.00", "status": "paid"}

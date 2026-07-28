from datetime import date
from decimal import Decimal

import pytest

from adwatch.ledger.models import ExpenseDraft
from adwatch.ledger.service import LedgerError, LedgerService
from adwatch.storage.db import Database


@pytest.fixture
def service(tmp_path):
    database = Database(tmp_path / "ledger.sqlite3")
    database.migrate()
    return LedgerService(database), database


def test_confirmed_expense_posts_cash_and_reversal_preserves_original(service):
    ledger, database = service
    entry = ledger.create_expense(
        ExpenseDraft(
            occurred_on=date(2026, 7, 28),
            category="物流",
            amount_original=Decimal(100),
            currency="CNY",
            rate_to_cny=Decimal(1),
            payer="洁云",
            fund_nature="operating_expense",
            affects_profit=True,
            affects_capital=False,
        ),
        actor="yl",
    )

    ledger.confirm_expense(entry.id, actor="yl")
    ledger.reverse_expense(entry.id, actor="yl", reason="重复录入")

    with database.connect() as connection:
        expense = connection.execute(
            "SELECT status FROM expense_entries WHERE id=?", (entry.id,)
        ).fetchone()
        cash = connection.execute(
            "SELECT amount_cny FROM cash_movements ORDER BY created_at, id"
        ).fetchall()
        audits = connection.execute(
            "SELECT action FROM audit_events WHERE object_id=? ORDER BY created_at",
            (entry.id,),
        ).fetchall()

    assert expense["status"] == "reversed"
    assert [row["amount_cny"] for row in cash] == ["-100.00", "100.00"]
    assert [row["action"] for row in audits] == [
        "create",
        "confirm",
        "reverse",
    ]


def test_expense_rejects_invalid_amount_and_state_transitions(service):
    ledger, _ = service
    with pytest.raises(LedgerError, match="positive"):
        ledger.create_expense(
            ExpenseDraft(
                occurred_on=date(2026, 7, 28),
                category="物流",
                amount_original=Decimal(-1),
                currency="CNY",
                rate_to_cny=Decimal(1),
                payer="洁云",
                fund_nature="operating_expense",
                affects_profit=True,
                affects_capital=False,
            ),
            actor="yl",
        )


def test_capital_withdrawal_ad_funding_and_review_cost_are_writable(service):
    ledger, database = service
    capital = ledger.create_capital(
        partner="洁云",
        entry_type="paid_in",
        amount=Decimal(1000),
        occurred_on=date(2026, 7, 28),
        actor="yl",
    )
    withdrawal = ledger.create_withdrawal(
        partner="苏姐",
        amount=Decimal(100),
        occurred_on=date(2026, 7, 28),
        purpose="备用金",
        actor="yl",
    )
    funding = ledger.create_ad_funding(
        platform="shopee",
        store="shop",
        entry_type="recharge",
        amount=Decimal(500),
        occurred_on=date(2026, 7, 28),
        source="manual",
        actor="yl",
    )
    review = ledger.create_review_order_cost(
        platform="shopee",
        store="shop",
        order_id="REVIEW-1",
        seller_sku="SKU-1",
        goods_cost=Decimal(20),
        service_fee=Decimal(5),
        occurred_on=date(2026, 7, 28),
        actor="yl",
    )

    assert all((capital, withdrawal, funding, review))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capital_entries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM withdrawal_entries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ad_funding_entries").fetchone()[0] == 1
        row = connection.execute(
            "SELECT excluded_from_real_metrics FROM review_order_costs"
        ).fetchone()
    assert row["excluded_from_real_metrics"] == 1

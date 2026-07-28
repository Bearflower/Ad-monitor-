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

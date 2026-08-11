from datetime import date
from decimal import Decimal

import pytest

from adwatch.evidence.service import EvidenceService
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


def test_expense_rejects_repeated_confirm_and_reversal(service):
    ledger, database = service
    entry = ledger.create_expense(
        ExpenseDraft(
            occurred_on=date(2026, 8, 4),
            category="物流",
            amount_original=Decimal(10),
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
    with pytest.raises(LedgerError, match="only draft"):
        ledger.confirm_expense(entry.id, actor="yl")
    ledger.reverse_expense(entry.id, actor="yl", reason="重复录入")
    with pytest.raises(LedgerError, match="only confirmed"):
        ledger.reverse_expense(entry.id, actor="yl", reason="再次冲销")

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM cash_movements WHERE source_id=?",
            (entry.id,),
        ).fetchone()[0]
    assert count == 2


def test_all_financial_facts_reverse_cash_once_and_store_actor(service):
    ledger, database = service
    capital = ledger.create_capital(
        partner="洁云",
        entry_type="paid_in",
        amount=Decimal(100),
        occurred_on=date(2026, 8, 4),
        actor="operator",
    )
    withdrawal = ledger.create_withdrawal(
        partner="洁云",
        amount=Decimal(30),
        occurred_on=date(2026, 8, 4),
        purpose="备用金",
        actor="operator",
    )
    funding = ledger.create_ad_funding(
        platform="shopee",
        store="shop",
        entry_type="recharge",
        amount=Decimal(20),
        occurred_on=date(2026, 8, 4),
        source="manual",
        actor="operator",
    )
    review = ledger.create_review_order_cost(
        platform="shopee",
        store="shop",
        order_id="REVIEW-2",
        seller_sku="SKU-2",
        goods_cost=Decimal(8),
        service_fee=Decimal(2),
        occurred_on=date(2026, 8, 4),
        actor="operator",
    )

    reverse_calls = (
        (ledger.reverse_capital, capital),
        (ledger.reverse_withdrawal, withdrawal),
        (ledger.reverse_ad_funding, funding),
        (ledger.reverse_review_order_cost, review),
    )
    for reverse, entry_id in reverse_calls:
        reverse(entry_id, actor="reviewer", reason="录入错误")
        with pytest.raises(LedgerError, match="only confirmed"):
            reverse(entry_id, actor="reviewer", reason="重复冲销")

    with database.connect() as connection:
        stored = {
            name: tuple(
                connection.execute(
                    f"SELECT status, created_by, reversal_reason "
                    f"FROM {table} WHERE id=?",
                    (entry_id,),
                ).fetchone()
            )
            for name, table, entry_id in (
                ("capital", "capital_entries", capital),
                ("withdrawal", "withdrawal_entries", withdrawal),
                ("ad", "ad_funding_entries", funding),
                ("review", "review_order_costs", review),
            )
        }
        cash_total = connection.execute(
            "SELECT SUM(CAST(amount_cny AS REAL)) FROM cash_movements"
        ).fetchone()[0]
        reverse_audits = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='reverse'"
        ).fetchone()[0]

    assert set(stored.values()) == {("reversed", "operator", "录入错误")}
    assert Decimal(str(cash_total)) == Decimal(0)
    assert reverse_audits == 4
    views = (
        ledger.list_capital_entries()[0],
        ledger.list_withdrawal_entries()[0],
        ledger.list_ad_funding_entries()[0],
        ledger.list_review_order_costs()[0],
    )
    assert all(view["status"] == "reversed" for view in views)
    assert all(view["created_by"] == "operator" for view in views)
    assert all(view["reversal_reason"] == "录入错误" for view in views)
    assert all(view["evidence_ids"] == () for view in views)
    assert all("stored_name" not in view for view in views)


def test_income_and_cash_adjustments_are_append_only_and_reversible(service):
    ledger, database = service
    income = ledger.create_adjustment(
        adjustment_type="income",
        amount_cny=Decimal(25),
        occurred_on=date(2026, 8, 4),
        reason="补记收入",
        actor="operator",
    )
    cash = ledger.create_adjustment(
        adjustment_type="cash",
        amount_cny=Decimal(-5),
        occurred_on=date(2026, 8, 4),
        reason="银行手续费",
        actor="operator",
    )
    reversal = ledger.reverse_adjustment(
        income,
        occurred_on=date(2026, 8, 4),
        actor="reviewer",
        reason="收入重复",
    )
    with pytest.raises(LedgerError, match="already reversed"):
        ledger.reverse_adjustment(
            income,
            occurred_on=date(2026, 8, 4),
            actor="reviewer",
            reason="再次冲销",
        )

    with database.connect() as connection:
        adjustments = connection.execute(
            """
            SELECT id, amount_cny, status, reversal_of, created_by
            FROM financial_adjustments ORDER BY created_at, id
            """
        ).fetchall()
        cash_total = connection.execute(
            "SELECT SUM(CAST(amount_cny AS REAL)) FROM cash_movements"
        ).fetchone()[0]

    assert {row["id"] for row in adjustments} == {income, cash, reversal}
    reverse_row = next(row for row in adjustments if row["id"] == reversal)
    assert tuple(reverse_row)[1:] == (
        "-25.00",
        "reversed",
        income,
        "reviewer",
    )
    assert Decimal(str(cash_total)) == Decimal(-5)


def test_adjustment_query_exposes_evidence_ids_without_storage_paths(service):
    ledger, database = service
    adjustment = ledger.create_adjustment(
        adjustment_type="income",
        amount_cny=Decimal(25),
        occurred_on=date(2026, 8, 4),
        reason="补记收入",
        actor="operator",
    )
    document = EvidenceService(database, ledger.database.path.parent / "evidence").attach(
        object_type="financial_adjustment",
        object_id=adjustment,
        display_name="proof.pdf",
        media_type="application/pdf",
        content=b"%PDF-1.7\nproof",
        created_by="operator",
    )

    rows = ledger.list_adjustments()

    assert rows[0].evidence_ids == (document.id,)
    assert not hasattr(rows[0], "stored_name")

from datetime import date
from decimal import Decimal

import pytest

from adwatch.ledger.service import LedgerService
from adwatch.reconciliation.balances import (
    BalanceReconciliationError,
    BalanceReconciliationService,
)
from adwatch.storage.db import Database


@pytest.fixture
def balances(tmp_path):
    database = Database(tmp_path / "balances.sqlite3")
    database.migrate()
    return BalanceReconciliationService(database), LedgerService(database), database


def _snapshot(
    service,
    account_type: str,
    amount: Decimal,
    *,
    account_name: str | None = None,
):
    return service.record_snapshot(
        account_type=account_type,
        account_name=account_name or account_type,
        snapshot_date=date(2026, 8, 4),
        amount_cny=amount,
        source="manual",
        actor="operator",
    )


def test_reconciliation_selects_latest_per_account_and_same_day_revision(balances):
    service, ledger, _ = balances
    ledger.create_adjustment(
        adjustment_type="cash",
        amount_cny=Decimal(100),
        occurred_on=date(2026, 8, 4),
        reason="期初现金",
        actor="operator",
    )
    _snapshot(service, "cash", Decimal(100))
    _snapshot(service, "platform", Decimal(40), account_name="shop-a")
    _snapshot(service, "platform", Decimal(58), account_name="shop-b")
    _snapshot(service, "bank", Decimal(100))

    first = service.reconcile(data_date=date(2026, 8, 4), actor="reviewer")
    _snapshot(service, "platform", Decimal(60), account_name="shop-b")
    second = service.reconcile(data_date=date(2026, 8, 4), actor="reviewer")

    assert first.status == "open"
    assert first.platform_observed_cny == Decimal("98.00")
    assert first.platform_difference_cny == Decimal("-2.00")
    assert second.status == "resolved"
    assert second.platform_observed_cny == Decimal("100.00")
    assert second.platform_difference_cny == Decimal("0.00")


def test_missing_snapshots_remain_none_instead_of_becoming_zero(balances):
    service, _, _ = balances

    empty = service.reconcile(data_date=date(2026, 8, 3), actor="reviewer")
    _snapshot(service, "cash", Decimal(0))
    _snapshot(service, "platform", Decimal(0))
    partial = service.reconcile(data_date=date(2026, 8, 4), actor="reviewer")

    assert empty.status == "pending_data"
    assert empty.snapshot_state == "none"
    assert empty.cash_observed_cny is None
    assert empty.cash_difference_cny is None
    assert partial.status == "pending_data"
    assert partial.snapshot_state == "partial"
    assert partial.bank_observed_cny is None
    assert partial.bank_difference_cny is None


def test_decimal_differences_do_not_use_binary_float(balances):
    service, ledger, _ = balances
    ledger.create_adjustment(
        adjustment_type="cash",
        amount_cny=Decimal("0.10"),
        occurred_on=date(2026, 8, 4),
        reason="现金一",
        actor="operator",
    )
    ledger.create_adjustment(
        adjustment_type="cash",
        amount_cny=Decimal("0.20"),
        occurred_on=date(2026, 8, 4),
        reason="现金二",
        actor="operator",
    )
    for account_type in ("cash", "platform", "bank"):
        _snapshot(service, account_type, Decimal("0.30"))

    result = service.reconcile(data_date=date(2026, 8, 4), actor="reviewer")

    assert result.ledger_cash_cny == Decimal("0.30")
    assert result.cash_difference_cny == Decimal("0.00")
    assert result.status == "resolved"


def test_resolution_appends_linked_record_and_keeps_original_immutable(balances):
    service, ledger, database = balances
    ledger.create_adjustment(
        adjustment_type="cash",
        amount_cny=Decimal(100),
        occurred_on=date(2026, 8, 4),
        reason="期初现金",
        actor="operator",
    )
    _snapshot(service, "cash", Decimal(90))
    _snapshot(service, "platform", Decimal(100))
    _snapshot(service, "bank", Decimal(100))
    original = service.reconcile(
        data_date=date(2026, 8, 4), actor="reviewer"
    )
    adjustment = ledger.create_adjustment(
        adjustment_type="balance",
        amount_cny=Decimal(-10),
        occurred_on=date(2026, 8, 4),
        reason="账实修复",
        actor="reviewer",
    )

    resolved = service.resolve(
        original.id,
        adjustment_id=adjustment,
        actor="reviewer",
    )

    assert resolved.status == "resolved"
    assert resolved.resolution_adjustment_id == adjustment
    assert resolved.supersedes_id == original.id
    with pytest.raises(BalanceReconciliationError, match="already resolved"):
        service.resolve(
            original.id,
            adjustment_id=adjustment,
            actor="reviewer",
        )
    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT status, resolution_adjustment_id, supersedes_id
            FROM balance_reconciliations WHERE id=?
            """,
            (original.id,),
        ).fetchone()
    assert tuple(stored) == ("open", None, None)


def test_resolution_requires_real_confirmed_financial_adjustment(balances):
    service, _, _ = balances
    _snapshot(service, "cash", Decimal(1))
    _snapshot(service, "platform", Decimal(2))
    _snapshot(service, "bank", Decimal(3))
    original = service.reconcile(
        data_date=date(2026, 8, 4), actor="reviewer"
    )

    with pytest.raises(BalanceReconciliationError, match="adjustment"):
        service.resolve(original.id, adjustment_id="missing", actor="reviewer")

from datetime import date
from decimal import Decimal

import pytest

from adwatch.ledger.service import LedgerService
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


def test_period_from_ledger_excludes_purchase_and_ad_recharge(sharing):
    service, database = sharing
    service.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal("0.60"), "苏姐": Decimal("0.40")},
        actor="yl",
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO settlement_records VALUES(
              's1','shopee','shop','O1','2026-07-10',
              '1000','CNY','1','1000','cli','2026-07-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','O1','SKU-1',1,'300','300',NULL,
              'confirmed','2026-08-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO inventory_movements(
              id, seller_sku, movement_type, quantity_delta, occurred_on,
              source_type, source_id, note, created_at
            ) VALUES (
              'move1','SKU-1','sale_out',-1,'2026-07-10','order',
              'shopee:shop:O1','','2026-08-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO ad_spend_entries VALUES(
              'ad1','shopee','shop','C1','100','CNY','1','100',
              '2026-07-10','cli','ad-ext','2026-07-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO purchase_receipts(
              id, supplier, received_on, status, created_by, created_at
            ) VALUES (
              'PO1','factory','2026-07-01','confirmed','yl',
              '2026-07-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO ad_funding_entries(
              id, platform, store, entry_type, amount_original, currency,
              rate_to_cny, amount_cny, occurred_on, source, external_key,
              status, created_at
            ) VALUES (
              'fund1','shopee','shop','recharge','500','CNY','1','500',
              '2026-07-01','manual',NULL,'confirmed',
              '2026-07-01T00:00:00Z')
            """
        )
    period = service.create_period_from_ledger(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        actor="yl",
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT net_profit_cny FROM profit_periods WHERE id=?", (period,)
        ).fetchone()
    assert row["net_profit_cny"] == "600.00"


def test_supplier_fulfilled_order_cost_is_included_by_order_date(sharing):
    service, database = sharing
    service.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal("0.60"), "苏姐": Decimal("0.40")},
        actor="yl",
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO settlement_records VALUES(
              's1','shopee','shop','O1','2026-07-10',
              '100','CNY','1','100','cli','2026-07-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','O1','item','model','SKU-1','1 bag','Product',
              1,'100','CNY','completed','delivered','','2026-07-10',
              '2026-07-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
              'shopee','shop','O1','SKU-1','supplier_fulfilled',
              '2026-07-01','available','sku_policy',
              '2026-07-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','O1','SKU-1',1,'30','30','2026-07-01',
              'confirmed','2026-08-01T00:00:00Z')
            """
        )

    period = service.create_period_from_ledger(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        actor="yl",
    )

    with database.connect() as connection:
        net_profit = connection.execute(
            "SELECT net_profit_cny FROM profit_periods WHERE id=?",
            (period,),
        ).fetchone()[0]
    assert net_profit == "70.00"


def test_ambiguous_resolved_order_is_excluded_from_profit_sharing_cogs(sharing):
    service, database = sharing
    service.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal("0.60"), "苏姐": Decimal("0.40")},
        actor="yl",
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO settlement_records VALUES(
              's1','shopee','shop','O1','2026-07-10',
              '100','CNY','1','100','cli','2026-07-10T00:00:00Z')
            """
        )
        for item_id, model_id, updated_at in (
            ("item-1", "model-1", "2026-07-10T00:00:00Z"),
            ("item-2", "model-2", "2026-07-11T00:00:00Z"),
        ):
            connection.execute(
                """
                INSERT INTO platform_order_lines VALUES(
                  'shopee','shop','O1',?,?, 'SKU-1','1 bag','Product',
                  1,'100','CNY','completed','delivered','','2026-07-10',?)
                """,
                (item_id, model_id, updated_at),
            )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
              'shopee','shop','O1','SKU-1','supplier_fulfilled',
              '2026-07-01','available','sku_policy',
              '2026-07-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','O1','SKU-1',1,'30','30','2026-07-01',
              'confirmed','2026-08-01T00:00:00Z')
            """
        )

    period = service.create_period_from_ledger(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        actor="yl",
    )

    with database.connect() as connection:
        net_profit = connection.execute(
            "SELECT net_profit_cny FROM profit_periods WHERE id=?",
            (period,),
        ).fetchone()[0]
    assert net_profit == "100.00"


def test_period_profit_includes_net_confirmed_income_adjustments(sharing):
    service, database = sharing
    service.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal("0.60"), "苏姐": Decimal("0.40")},
        actor="yl",
    )
    ledger = LedgerService(database)
    adjustment = ledger.create_adjustment(
        adjustment_type="income",
        amount_cny=Decimal(25),
        occurred_on=date(2026, 7, 10),
        reason="补记收入",
        actor="yl",
    )

    first = service.create_period_from_ledger(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        actor="yl",
    )
    ledger.reverse_adjustment(
        adjustment,
        occurred_on=date(2026, 7, 10),
        actor="yl",
        reason="重复收入",
    )
    second = service.create_period_from_ledger(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        actor="yl",
    )

    with database.connect() as connection:
        amounts = {
            row["id"]: row["net_profit_cny"]
            for row in connection.execute(
                "SELECT id, net_profit_cny FROM profit_periods"
            )
        }
    assert amounts[first] == "25.00"
    assert amounts[second] == "0.00"


def _confirmed_period(service: ProfitSharingService) -> str:
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
    return period


def test_payment_cannot_be_created_directly_as_reversed(sharing):
    service, _ = sharing
    period = _confirmed_period(service)

    with pytest.raises(ProfitSharingError, match="invalid payment"):
        service.record_payment(
            period_id=period,
            partner="洁云",
            amount_cny=Decimal(10),
            paid_on=date(2026, 8, 4),
            status="reversed",
            note="非法",
            actor="yl",
        )


def test_paid_payment_reversal_is_append_only_and_restores_allowance(sharing):
    service, database = sharing
    period = _confirmed_period(service)
    payment = service.record_payment(
        period_id=period,
        partner="洁云",
        amount_cny=Decimal(500),
        paid_on=date(2026, 8, 4),
        status="paid",
        note="首笔",
        actor="operator",
    )
    reversal = service.reverse_payment(
        payment,
        reversed_on=date(2026, 8, 4),
        reason="账户错误",
        actor="reviewer",
    )
    with pytest.raises(ProfitSharingError, match="already reversed"):
        service.reverse_payment(
            payment,
            reversed_on=date(2026, 8, 4),
            reason="重复冲销",
            actor="reviewer",
        )
    replacement = service.record_payment(
        period_id=period,
        partner="洁云",
        amount_cny=Decimal(600),
        paid_on=date(2026, 8, 4),
        status="paid",
        note="更正付款",
        actor="operator",
    )

    with database.connect() as connection:
        reverse_row = connection.execute(
            """
            SELECT amount_cny, status, reversal_of, created_by,
                   reversal_reason
            FROM profit_payments WHERE id=?
            """,
            (reversal,),
        ).fetchone()
        cash = connection.execute(
            """
            SELECT amount_cny FROM cash_movements
            WHERE source_type='profit_payment' ORDER BY created_at, id
            """
        ).fetchall()
    assert tuple(reverse_row) == (
        "-500.00",
        "reversed",
        payment,
        "reviewer",
        "账户错误",
    )
    assert [row["amount_cny"] for row in cash] == [
        "-500.00",
        "500.00",
        "-600.00",
    ]
    assert replacement
    listed = service.list_profit_payments(period_id=period)
    assert {row["created_by"] for row in listed} == {"operator", "reviewer"}
    assert all("stored_name" not in row for row in listed)


def test_confirmed_period_requires_paid_records_to_be_reversed_first(sharing):
    service, database = sharing
    period = _confirmed_period(service)
    payment = service.record_payment(
        period_id=period,
        partner="洁云",
        amount_cny=Decimal(100),
        paid_on=date(2026, 8, 4),
        status="paid",
        note="付款",
        actor="operator",
    )

    with pytest.raises(ProfitSharingError, match="unreversed payments"):
        service.reverse_period(period, actor="reviewer", reason="期间错误")
    service.reverse_payment(
        payment,
        reversed_on=date(2026, 8, 4),
        reason="先冲付款",
        actor="reviewer",
    )
    service.reverse_period(period, actor="reviewer", reason="期间错误")

    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT status, reversal_reason FROM profit_periods WHERE id=?
            """,
            (period,),
        ).fetchone()
        allocation_count = connection.execute(
            "SELECT COUNT(*) FROM profit_allocations WHERE period_id=?",
            (period,),
        ).fetchone()[0]
        audit_actions = {
            (row["object_type"], row["action"])
            for row in connection.execute(
                "SELECT object_type, action FROM audit_events"
            )
        }
    assert tuple(stored) == ("reversed", "期间错误")
    assert allocation_count == 2
    assert {
        ("profit_agreement", "create"),
        ("profit_period", "create"),
        ("profit_period", "confirm"),
        ("profit_payment", "create"),
        ("profit_payment", "reverse"),
        ("profit_period", "reverse"),
    } <= audit_actions

from datetime import date
from decimal import Decimal

from adwatch.dashboard.routes import DashboardRouter
from adwatch.dashboard.views import (
    render_ad_funds_page,
    render_inventory_page,
    render_profit_sharing_page,
)
from adwatch.evidence.service import EvidenceService
from adwatch.inventory.service import InventoryService
from adwatch.ledger.service import LedgerService
from adwatch.profit_sharing.service import ProfitSharingService
from adwatch.reconciliation.balances import BalanceReconciliationService
from adwatch.storage.db import Database


def test_finance_inventory_and_profit_closure_runs_twice_without_fact_loss(
    tmp_path,
):
    database = Database(tmp_path / "acceptance.sqlite3")
    database.migrate()
    ledger = LedgerService(database)
    inventory = InventoryService(database)
    sharing = ProfitSharingService(database)
    balances = BalanceReconciliationService(database)
    evidence = EvidenceService(database, tmp_path / "evidence")
    router = DashboardRouter(
        ledger,
        csrf_token="acceptance-csrf",
        inventory=inventory,
        profit_sharing=sharing,
        balances=balances,
    )
    common = {"csrf_token": "acceptance-csrf"}
    sharing.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal(1)},
        actor="acceptance",
    )

    for offset, run_date in enumerate((date(2026, 8, 3), date(2026, 8, 4)), 1):
        assert router.post(
            "/expenses",
            {
                **common,
                "occurred_on": run_date.isoformat(),
                "category": f"验收费用 {offset}",
                "amount": "10",
                "currency": "CNY",
                "rate_to_cny": "1",
                "payer": "洁云",
                "fund_nature": "operating_expense",
                "affects_profit": "1",
            },
        ).status == 303
        with database.connect() as connection:
            expense_id = connection.execute(
                """
                SELECT id FROM expense_entries WHERE category=?
                """,
                (f"验收费用 {offset}",),
            ).fetchone()[0]
        assert router.post(
            "/expenses/confirm", {**common, "entry_id": expense_id}
        ).status == 303
        document = evidence.attach(
            object_type="expense",
            object_id=expense_id,
            display_name=f"expense-{offset}.pdf",
            media_type="application/pdf",
            content=f"%PDF-1.7\nrun-{offset}".encode(),
            created_by="acceptance",
        )
        assert evidence.read(document.id).content.endswith(
            f"run-{offset}".encode()
        )
        assert router.post(
            "/expenses/reverse",
            {**common, "entry_id": expense_id, "reason": "验收冲销"},
        ).status == 303
        assert router.post(
            "/expenses/reverse",
            {**common, "entry_id": expense_id, "reason": "重复"},
        ).status == 400

        for account_type, amount in (
            ("cash", "0"),
            ("platform", "5"),
            ("bank", "0"),
        ):
            assert router.post(
                "/balance-snapshots",
                {
                    **common,
                    "account_type": account_type,
                    "account_name": f"{account_type}-{offset}",
                    "snapshot_date": run_date.isoformat(),
                    "amount_cny": amount,
                    "source": "acceptance",
                },
            ).status == 303
        assert router.post(
            "/balance-reconciliations",
            {**common, "data_date": run_date.isoformat()},
        ).status == 303
        reconciliation = balances.list_reconciliations(data_date=run_date)[0]
        assert reconciliation.status == "open"
        assert router.post(
            "/adjustments",
            {
                **common,
                "adjustment_type": "balance",
                "amount_cny": "5",
                "occurred_on": run_date.isoformat(),
                "reason": f"验收余额修复 {offset}",
            },
        ).status == 303
        adjustment = next(
            item
            for item in ledger.list_adjustments()
            if item.reason == f"验收余额修复 {offset}"
        )
        assert router.post(
            "/balance-reconciliations/resolve",
            {
                **common,
                "reconciliation_id": reconciliation.id,
                "adjustment_id": adjustment.id,
            },
        ).status == 303
        assert router.post(
            "/balance-reconciliations/resolve",
            {
                **common,
                "reconciliation_id": reconciliation.id,
                "adjustment_id": adjustment.id,
            },
        ).status == 400

        assert router.post(
            "/inventory/adjust",
            {
                **common,
                "seller_sku": f"SKU-{offset}",
                "quantity_delta": "3",
                "occurred_on": run_date.isoformat(),
                "reason": "验收盘点",
            },
        ).status == 303
        movement = next(
            item
            for item in inventory.list_inventory_movements()
            if item["seller_sku"] == f"SKU-{offset}"
            and item["movement_type"] == "inventory_adjustment"
        )
        assert router.post(
            "/inventory/movements/reverse",
            {
                **common,
                "movement_id": movement["id"],
                "occurred_on": run_date.isoformat(),
                "reason": "验收库存冲销",
            },
        ).status == 303
        assert inventory.balance(f"SKU-{offset}") == 0

        period = sharing.create_period(
            starts_on=run_date,
            ends_on=run_date,
            net_profit_cny=Decimal(100),
            actor="acceptance",
        )
        sharing.confirm_period(period, actor="acceptance")
        payment = sharing.record_payment(
            period_id=period,
            partner="洁云",
            amount_cny=Decimal(20),
            paid_on=run_date,
            status="paid",
            note="验收付款",
            actor="acceptance",
        )
        assert router.post(
            "/profit-payments/reverse",
            {
                **common,
                "payment_id": payment,
                "reversed_on": run_date.isoformat(),
                "reason": "验收付款冲销",
            },
        ).status == 303

        assert f'value="expense:{expense_id}"' in render_ad_funds_page(
            database, run_date, csrf_token="acceptance-csrf"
        )
        assert f"SKU-{offset}" in render_inventory_page(
            database, run_date, csrf_token="acceptance-csrf"
        )
        assert f'value="{period}"' in render_profit_sharing_page(
            database, run_date, csrf_token="acceptance-csrf"
        )

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM expense_entries"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence_documents"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM balance_reconciliations"
        ).fetchone()[0] == 4
        assert connection.execute(
            "SELECT COUNT(*) FROM profit_payments"
        ).fetchone()[0] == 4

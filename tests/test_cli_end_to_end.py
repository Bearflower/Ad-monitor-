import json
from datetime import date
from decimal import Decimal

from adwatch.cli import main
from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryService
from adwatch.ledger.models import ExpenseDraft
from adwatch.ledger.service import LedgerService
from adwatch.optimization.models import OptimizationInput
from adwatch.optimization.service import analyze_optimization
from adwatch.reconciliation.service import ReconciliationService
from adwatch.storage.db import Database


def test_mock_collection_writes_database_and_quality_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert main(["init"]) == 0
    assert main(["collect", "--mode", "mock", "--date", "2026-07-22"]) == 0

    report = json.loads(
        (tmp_path / "reports" / "quality-2026-07-22.json").read_text()
    )
    assert {item["platform"] for item in report["runs"]} == {
        "tiktok",
        "shopee",
    }
    assert report["totals"]["accepted"] >= 8
    assert report["totals"]["quarantined"] == 0


def test_unified_business_flow_keeps_inventory_profit_and_live_gate_separate(
    tmp_path,
):
    database = Database(tmp_path / "unified.sqlite3")
    database.migrate()
    ledger = LedgerService(database)
    expense = ledger.create_expense(
        ExpenseDraft(
            date(2026, 7, 28),
            "包装",
            Decimal(10),
            "CNY",
            Decimal(1),
            "洁云",
            "operating_expense",
            True,
            False,
        ),
        actor="yl",
    )
    ledger.confirm_expense(expense.id, actor="yl")
    inventory = InventoryService(database)
    inventory.receive_purchase(
        receipt_id="PO-1",
        supplier="工厂",
        received_on=date(2026, 7, 27),
        lines=(PurchaseLine("SKU-1", 10, Decimal(5)),),
        actor="yl",
    )
    inventory.ship_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=2,
        shipped_on=date(2026, 7, 28),
        unit_cost_cny=Decimal(5),
    )
    result = analyze_optimization(
        OptimizationInput(
            Decimal(100),
            Decimal(20),
            Decimal(90),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(10),
            Decimal(5),
            Decimal(0),
            Decimal(0),
            8,
            Decimal(1),
            "campaign_only",
        )
    )
    reconcile = ReconciliationService(database)
    reconcile.record_day(
        platform="shopee",
        store="shop",
        data_date=date(2026, 7, 28),
        expected={"orders": 1},
        actual={"orders": 1},
    )

    assert inventory.balance("SKU-1") == 8
    assert result.post_ad_net_profit == Decimal(55)
    assert not reconcile.three_day_ready(
        platform="shopee", store="shop", through=date(2026, 7, 28)
    )

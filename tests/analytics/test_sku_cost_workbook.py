from datetime import UTC, date, datetime
from decimal import Decimal

from openpyxl import Workbook, load_workbook

from adwatch.analytics.sku_cost_workbook import (
    export_pending_sku_costs,
    import_sku_costs,
)
from adwatch.orders.models import PlatformOrderLine, PlatformSku
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


def test_pending_workbook_prefills_facts_and_imports_only_cost(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "虾皮泰国", "57861884313", "311033956020",
                "Foot Soak Bag-one bag", "1 bag", "泡脚包", 31,
                datetime(2026, 7, 28, 9, tzinfo=UTC),
            ),
        )
    )
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "虾皮泰国", "ORDER-1", "57861884313",
                "311033956020", "Foot Soak Bag-one bag", "1 bag",
                "泡脚包", 1, Decimal(100), "THB", "completed", "delivered",
                "", date(2026, 7, 28),
                datetime(2026, 7, 28, 9, tzinfo=UTC),
            ),
        )
    )
    target = tmp_path / "pending.xlsx"

    assert export_pending_sku_costs(database, target) == 1
    workbook = load_workbook(target)
    sheet = workbook["待补成本"]
    assert sheet.freeze_panes == "A2"
    assert sheet["F2"].value == "Foot Soak Bag-one bag"
    assert sheet["H2"].value == 31

    sheet["O2"] = 5
    sheet["P2"] = "2026-07-08"
    workbook.save(target)
    assert import_sku_costs(database, target) == 1

    with database.connect() as connection:
        row = connection.execute("SELECT * FROM sku_cost_history").fetchone()
    assert Decimal(row["unit_cost_cny"]) == Decimal(5)
    assert row["effective_date"] == "2026-07-08"


def test_pending_workbook_excludes_never_ordered_catalog_skus(tmp_path):
    database = Database(tmp_path / "unsold.sqlite3")
    database.migrate()
    OrderRepository(database).upsert_skus(
        (
            PlatformSku(
                "shopee", "shop", "item", "model", "UNSOLD", "",
                "Never ordered", 12,
                datetime(2026, 8, 10, 9, tzinfo=UTC),
            ),
        )
    )

    assert export_pending_sku_costs(database, tmp_path / "pending.xlsx") == 0


def test_imports_simplified_user_facing_cost_workbook(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    target = tmp_path / "pending-simple.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "待补SKU成本"
    sheet.append(["说明"])
    sheet.append([])
    sheet.append([])
    sheet.append([])
    sheet.append([
        "平台", "店铺", "Seller SKU", "规格", "商品名称",
        "首次订单日期", "最近订单日期", "相关订单数", "销售件数",
        "退货件数", "净件数", "成本状态", "单件成本（人民币）",
        "成本生效日期", "成本备注",
    ])
    sheet.append([
        "Shopee", "no4kud44da", "0500106001010", "Black,140cm",
        "Leather Belt", "2026-04-10", "2026-05-07", 14, 14, 1, 13,
        "已填写", 12.5, "2026-04-10", "首批订单成本",
    ])
    workbook.save(target)

    assert import_sku_costs(database, target) == 1

    with database.connect() as connection:
        row = connection.execute("SELECT * FROM sku_cost_history").fetchone()
    assert row["platform"] == "shopee"
    assert row["store"] == "no4kud44da"
    assert row["seller_sku"] == "0500106001010"
    assert Decimal(row["unit_cost_cny"]) == Decimal("12.5")
    assert row["effective_date"] == "2026-04-10"


def test_workbook_uses_distinct_cost_keys_for_reused_seller_sku(tmp_path):
    database = Database(tmp_path / "duplicate-seller-sku.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "shop", "item-1", "model-1", "Chief",
                "Chief 50ml", "Roll On", 10,
                datetime(2026, 8, 10, tzinfo=UTC),
            ),
            PlatformSku(
                "shopee", "shop", "item-2", "model-2", "Chief",
                "Chief 250ml", "Body Spray", 20,
                datetime(2026, 8, 10, tzinfo=UTC),
            ),
        )
    )
    observed_at = datetime(2026, 8, 10, tzinfo=UTC)
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "shop", "ORDER-50", "item-1", "model-1",
                "Chief", "Chief 50ml", "Roll On", 1, Decimal(100), "THB",
                "completed", "delivered", "", date(2026, 8, 10), observed_at,
            ),
            PlatformOrderLine(
                "shopee", "shop", "ORDER-250", "item-2", "model-2",
                "Chief", "Chief 250ml", "Body Spray", 1, Decimal(100),
                "THB", "completed", "delivered", "", date(2026, 8, 10),
                observed_at,
            ),
        )
    )
    target = tmp_path / "pending.xlsx"

    assert export_pending_sku_costs(database, target) == 2
    workbook = load_workbook(target)
    sheet = workbook["待补成本"]
    assert {sheet["F2"].value, sheet["F3"].value} == {
        "Chief [item-1:model-1]",
        "Chief [item-2:model-2]",
    }
    expected_costs = {
        "Chief [item-1:model-1]": 5,
        "Chief [item-2:model-2]": 9,
    }
    for row in range(2, 4):
        sheet.cell(row, 15).value = expected_costs[sheet.cell(row, 6).value]
        sheet.cell(row, 16).value = "2026-08-10"
    workbook.save(target)

    assert import_sku_costs(database, target) == 2
    with database.connect() as connection:
        costs = {
            row["seller_sku"]: row["unit_cost_cny"]
            for row in connection.execute(
                "SELECT seller_sku, unit_cost_cny FROM sku_cost_history"
            )
        }
    assert costs == {
        "Chief [item-1:model-1]": "5",
        "Chief [item-2:model-2]": "9",
    }

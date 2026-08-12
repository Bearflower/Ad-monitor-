from adwatch.accounting.excel_preview import ExcelPrototypePreview
from adwatch.storage.db import Database


def test_real_excel_preview_is_read_only_and_maps_all_sheets(tmp_path):
    source = "/Users/yl/Documents/跨境电商/Shopee_tiktok泰国店记账.xlsx"
    database = Database(tmp_path / "preview.sqlite3")
    database.migrate()
    with database.connect() as connection:
        before = connection.total_changes
    result = ExcelPrototypePreview(database).preview(source)
    assert result.sheet_names == (
        "收支总览", "收入明细", "进货成本", "出库产品", "费用支出",
        "广告充值", "刷单成本", "月度汇总", "合伙人分润", "汇率换算",
        "前期投入",
    )
    assert result.counts["valid"] >= 1
    assert any(row.sheet == "收入明细" for row in result.rows)
    with database.connect() as connection:
        assert connection.total_changes == before


def test_excel_preview_detects_database_duplicate_and_conflict(tmp_path):
    from datetime import date
    from decimal import Decimal

    from openpyxl import Workbook

    from adwatch.ledger.models import ManualIncomeDraft
    from adwatch.ledger.service import LedgerService

    database = Database(tmp_path / "preview.sqlite3")
    database.migrate()
    LedgerService(database).create_manual_income(
        ManualIncomeDraft(
            occurred_on=date(2026, 8, 5), amount_original=Decimal(100),
            currency="SGD", rate_to_cny=Decimal("5.6"), order_id="O-1",
        ),
        actor="test",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "收入明细"
    sheet.append(["日期", "平台", "订单编号", "结算金额(SGD)"])
    sheet.append([date(2026, 8, 5), "Shopee", "O-1", 100])
    sheet.append([date(2026, 8, 5), "Shopee", "O-1", 101])
    source = tmp_path / "source.xlsx"
    workbook.save(source)
    result = ExcelPrototypePreview(database).preview(source)
    assert result.counts["duplicate"] == 1
    assert result.counts["conflict"] == 1

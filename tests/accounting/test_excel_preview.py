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

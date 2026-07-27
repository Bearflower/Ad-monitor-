import csv
from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook

from adwatch.analytics.business_inputs import BusinessInputError
from adwatch.analytics.order_costs import import_order_costs
from adwatch.storage.db import Database

HEADERS = ("日期", "平台", "店铺", "订单号", "SKU", "数量", "单件成本_人民币")


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def test_import_xlsx_normalizes_dates_and_is_idempotent(tmp_path):
    source = tmp_path / "orders.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    sheet.append([20260708, "Shopee", "no4kud44da", "001", "1 bag", 1, 5])
    sheet.append([date(2026, 7, 9), "shopee", "no4kud44da", "002", "3 bags", 2, 11])
    sheet.append(
        [datetime(2026, 7, 10, 8), "shopee", "no4kud44da", "003", "5 bags", 1, 17]
    )
    workbook.save(source)

    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    first = import_order_costs(database, source)
    second = import_order_costs(database, source)

    assert first.read == 3
    assert first.inserted == 3
    assert first.updated == 0
    assert first.total_cost_cny == Decimal("44.00")
    assert first.start == date(2026, 7, 8)
    assert first.end == date(2026, 7, 10)
    assert second.inserted == 0
    assert second.updated == 3
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT order_id, order_date FROM order_cost_lines ORDER BY order_id"
        ).fetchall()
    assert [(row["order_id"], row["order_date"]) for row in rows] == [
        ("001", "2026-07-08"),
        ("002", "2026-07-09"),
        ("003", "2026-07-10"),
    ]


def test_import_csv_allows_same_order_with_multiple_skus(tmp_path):
    source = tmp_path / "orders.csv"
    _write_csv(
        source,
        [
            [20260708, "shopee", "s", "o", "1 bag", 1, 5],
            ["2026-07-08", "shopee", "s", "o", "3 bags", 1, 11],
        ],
    )
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    summary = import_order_costs(database, source)

    assert summary.inserted == 2
    assert summary.total_cost_cny == Decimal("16.00")


def test_identical_file_duplicates_are_folded(tmp_path):
    source = tmp_path / "orders.csv"
    row = [20260708, "shopee", "s", "o", "1 bag", 1, 5]
    _write_csv(source, [row, row])
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    summary = import_order_costs(database, source)

    assert summary.read == 2
    assert summary.inserted == 1
    assert summary.deduplicated == 1


def test_conflicting_file_duplicates_reject_whole_batch(tmp_path):
    source = tmp_path / "orders.csv"
    _write_csv(
        source,
        [
            [20260708, "shopee", "s", "o", "1 bag", 1, 5],
            [20260708, "shopee", "s", "o", "1 bag", 2, 5],
        ],
    )
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with pytest.raises(BusinessInputError, match="conflicting duplicate"):
        import_order_costs(database, source)

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_cost_lines"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "row",
    [
        ["", "shopee", "s", "o", "sku", 1, 5],
        [20260708, "amazon", "s", "o", "sku", 1, 5],
        [20260708, "shopee", "s", "o", "sku", 0, 5],
        [20260708, "shopee", "s", "o", "sku", 1.5, 5],
        [20260708, "shopee", "s", "o", "sku", 1, -1],
        [20260708, "shopee", "s", "o", "sku", 1, "1.12345"],
    ],
)
def test_invalid_rows_reject_whole_batch(tmp_path, row):
    source = tmp_path / "orders.csv"
    _write_csv(source, [row])
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with pytest.raises(BusinessInputError):
        import_order_costs(database, source)

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_cost_lines"
        ).fetchone()[0] == 0


def test_missing_columns_and_empty_data_are_rejected(tmp_path):
    missing = tmp_path / "missing.csv"
    missing.write_text("日期,平台\n20260708,shopee\n", encoding="utf-8")
    empty = tmp_path / "empty.csv"
    _write_csv(empty, [])
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with pytest.raises(BusinessInputError, match="missing columns"):
        import_order_costs(database, missing)
    with pytest.raises(BusinessInputError, match="no order rows"):
        import_order_costs(database, empty)

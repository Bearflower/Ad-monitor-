from datetime import datetime
from decimal import Decimal

from openpyxl import Workbook

from adwatch.orders.shopee_order_export import parse_order_export


def test_parse_order_export_aggregates_duplicate_sku_lines(tmp_path):
    source = tmp_path / "Order.all.20260729_20260729.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "หมายเลขคำสั่งซื้อ",
            "สถานะการสั่งซื้อ",
            "สถานะการคืนเงินหรือคืนสินค้า",
            "วันที่ทำการสั่งซื้อ",
            "ชื่อสินค้า",
            "เลขอ้างอิง SKU (SKU Reference No.)",
            "ชื่อตัวเลือก",
            "จำนวน",
            "ราคาสินค้าที่ชำระโดยผู้ซื้อ (THB)",
        ]
    )
    sheet.append(
        [
            "ORDER-1",
            "ที่ต้องจัดส่ง",
            "",
            "2026-07-29 08:30",
            "Product A",
            "SKU-A",
            "Red",
            2,
            "100.00",
        ]
    )
    sheet.append(
        [
            "ORDER-1",
            "ที่ต้องจัดส่ง",
            "",
            "2026-07-29 08:30",
            "Product A",
            "SKU-A",
            "Red",
            3,
            "150.00",
        ]
    )
    workbook.save(source)

    result = parse_order_export(
        source,
        store="no4kud44da",
        source_updated_at=datetime(2026, 7, 30, 9, 0),
    )

    assert result.rejected == ()
    assert len(result.skus) == 1
    assert result.skus[0].observed_at.date().isoformat() == "2026-07-29"
    assert len(result.orders) == 1
    order = result.orders[0]
    assert order.order_id == "ORDER-1"
    assert order.seller_sku == "SKU-A"
    assert order.quantity == 5
    assert order.buyer_paid == Decimal("250.00")
    assert order.order_status == "pending"
    assert order.logistics_status == "pending"
    assert order.ordered_at.isoformat() == "2026-07-29"


def test_parse_order_export_maps_cancelled_and_completed_statuses(tmp_path):
    source = tmp_path / "orders.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "หมายเลขคำสั่งซื้อ",
            "สถานะการสั่งซื้อ",
            "สถานะการคืนเงินหรือคืนสินค้า",
            "วันที่ทำการสั่งซื้อ",
            "ชื่อสินค้า",
            "เลขอ้างอิง SKU (SKU Reference No.)",
            "ชื่อตัวเลือก",
            "จำนวน",
            "ราคาสินค้าที่ชำระโดยผู้ซื้อ (THB)",
        ]
    )
    sheet.append(
        [
            "CANCELLED",
            "ยกเลิกแล้ว",
            "",
            "2026-07-29 08:30",
            "Product A",
            "SKU-A",
            "Red",
            1,
            10,
        ]
    )
    sheet.append(
        [
            "COMPLETED",
            "สำเร็จแล้ว",
            "",
            "2026-07-29 09:30",
            "Product B",
            "SKU-B",
            "Blue",
            1,
            20,
        ]
    )
    workbook.save(source)

    result = parse_order_export(
        source,
        store="no4kud44da",
        source_updated_at=datetime(2026, 7, 30, 9, 0),
    )

    statuses = {order.order_id: order.order_status for order in result.orders}
    assert statuses == {"CANCELLED": "cancelled", "COMPLETED": "completed"}

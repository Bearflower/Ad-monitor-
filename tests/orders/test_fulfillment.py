from datetime import date
from decimal import Decimal

from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


def test_policy_resolves_by_order_date_and_snapshot_is_immutable(tmp_path):
    database = Database(tmp_path / "fulfillment.sqlite3")
    database.migrate()
    service = FulfillmentService(database)
    service.set_policy(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        mode="supplier_fulfilled",
        supply_status="available",
        note="货盘",
    )
    service.set_policy(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 8, 1),
        mode="stocked",
        supply_status="available",
        note="开始备货",
    )

    first = service.snapshot_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        ordered_on=date(2026, 7, 20),
    )
    repeated = service.snapshot_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        ordered_on=date(2026, 8, 20),
    )

    assert first is not None
    assert first.mode == "supplier_fulfilled"
    assert repeated == first


def test_bulk_marker_uses_each_skus_earliest_cost_date(tmp_path):
    database = Database(tmp_path / "fulfillment.sqlite3")
    database.migrate()
    orders = OrderRepository(database)
    for sku, effective in (
        ("SKU-1", date(2026, 4, 1)),
        ("SKU-2", date(2026, 5, 1)),
    ):
        orders.set_sku_cost(
            platform="shopee",
            store="shop",
            seller_sku=sku,
            effective_date=effective,
            unit_cost_cny=Decimal(5),
        )

    service = FulfillmentService(database)
    assert (
        service.mark_current_skus_supplier_fulfilled(
            platform="shopee", store="shop", note="现有货盘"
        )
        == 2
    )
    assert (
        service.mark_current_skus_supplier_fulfilled(
            platform="shopee", store="shop", note="重复"
        )
        == 0
    )
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT seller_sku, effective_date, mode
            FROM sku_fulfillment_history ORDER BY seller_sku
            """
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("SKU-1", "2026-04-01", "supplier_fulfilled"),
        ("SKU-2", "2026-05-01", "supplier_fulfilled"),
    ]

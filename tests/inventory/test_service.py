from datetime import date
from decimal import Decimal

import pytest

from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryError, InventoryService
from adwatch.storage.db import Database


@pytest.fixture
def inventory(tmp_path):
    database = Database(tmp_path / "inventory.sqlite3")
    database.migrate()
    return InventoryService(database), database


def test_purchase_sale_return_and_damage_preserve_inventory_equation(inventory):
    service, _ = inventory
    service.receive_purchase(
        receipt_id="PO-1",
        supplier="工厂",
        received_on=date(2026, 7, 1),
        lines=(PurchaseLine("SKU-1", 10, Decimal(5)),),
        actor="yl",
    )
    service.ship_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=3,
        shipped_on=date(2026, 7, 2),
        unit_cost_cny=Decimal(5),
    )
    service.return_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=1,
        returned_on=date(2026, 7, 3),
    )
    service.damage(
        seller_sku="SKU-1",
        quantity=2,
        occurred_on=date(2026, 7, 4),
        reason="运输破损",
        actor="yl",
    )

    assert service.balance("SKU-1") == 6
    assert service.ship_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=3,
        shipped_on=date(2026, 7, 2),
        unit_cost_cny=Decimal(5),
    ) is False


def test_cancelled_order_does_not_ship_and_negative_stock_is_blocked(inventory):
    service, _ = inventory
    assert service.ship_order(
        platform="shopee",
        store="shop",
        order_id="CANCELLED",
        seller_sku="SKU-1",
        quantity=1,
        shipped_on=date(2026, 7, 2),
        unit_cost_cny=Decimal(5),
        order_status="cancelled",
    ) is False
    with pytest.raises(InventoryError, match="insufficient"):
        service.ship_order(
            platform="shopee",
            store="shop",
            order_id="ORDER-2",
            seller_sku="SKU-1",
            quantity=1,
            shipped_on=date(2026, 7, 2),
            unit_cost_cny=Decimal(5),
        )

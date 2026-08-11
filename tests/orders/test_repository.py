from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.orders.models import PlatformOrderLine, PlatformSku
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


def test_sku_upsert_is_idempotent_and_keeps_latest_inventory(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    first = PlatformSku(
        "shopee", "虾皮泰国", "item", "model", "seller-sku", "1 bag",
        "Foot soak", 31, datetime(2026, 7, 28, 9, tzinfo=UTC),
    )
    latest = PlatformSku(
        "shopee", "虾皮泰国", "item", "model", "seller-sku", "1 bag",
        "Foot soak", 29, datetime(2026, 7, 29, 9, tzinfo=UTC),
    )

    repository.upsert_skus((first,))
    repository.upsert_skus((latest,))

    pending = repository.pending_sku_costs()
    assert len(pending) == 1
    assert pending[0]["inventory_units"] == 29
    assert pending[0]["seller_sku"] == "seller-sku"


def test_sku_map_is_isolated_by_platform_and_store(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "shop-a", "item", "model", "SKU-A", "A",
                "Product A", 1, observed_at,
            ),
            PlatformSku(
                "shopee", "shop-b", "item", "model", "SKU-B", "B",
                "Product B", 1, observed_at,
            ),
        )
    )

    mapping = repository.sku_map(platform="shopee", store="shop-a")

    assert mapping == {
        ("item", "model"): ("SKU-A", "A", "Product A")
    }


def test_pending_sku_costs_excludes_skus_used_only_by_cancelled_orders(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed_at = datetime(2026, 7, 30, 9, tzinfo=UTC)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee",
                "虾皮泰国",
                "item-cancelled",
                "model-cancelled",
                "ST-OC1232-08A",
                "1 bag",
                "Cancelled product",
                1,
                observed_at,
            ),
            PlatformSku(
                "shopee",
                "虾皮泰国",
                "item-active",
                "model-active",
                "ACTIVE-SKU",
                "1 bag",
                "Active product",
                1,
                observed_at,
            ),
        )
    )
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee",
                "虾皮泰国",
                "ORDER-CANCELLED",
                "item-cancelled",
                "model-cancelled",
                "ST-OC1232-08A",
                "1 bag",
                "Cancelled product",
                1,
                Decimal(100),
                "THB",
                "cancelled",
                "cancelled",
                "",
                date(2026, 7, 29),
                observed_at,
            ),
            PlatformOrderLine(
                "shopee",
                "虾皮泰国",
                "ORDER-ACTIVE",
                "item-active",
                "model-active",
                "ACTIVE-SKU",
                "1 bag",
                "Active product",
                1,
                Decimal(100),
                "THB",
                "completed",
                "delivered",
                "",
                date(2026, 7, 29),
                observed_at,
            ),
        )
    )

    pending = repository.pending_sku_costs()

    assert [row["seller_sku"] for row in pending] == ["ACTIVE-SKU"]


def test_missing_costs_require_version_effective_on_order_date(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee",
                "虾皮泰国",
                "item",
                "model",
                "SKU-A",
                "Blue",
                "Product",
                5,
                observed_at,
            ),
        )
    )
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee",
                "虾皮泰国",
                "260801ORDER",
                "item",
                "model",
                "SKU-A",
                "Blue",
                "Product",
                1,
                Decimal(100),
                "THB",
                "completed",
                "delivered",
                "",
                date(2026, 8, 1),
                observed_at,
            ),
        )
    )
    repository.set_sku_cost(
        platform="shopee",
        store="虾皮泰国",
        seller_sku="SKU-A",
        effective_date=date(2026, 8, 2),
        unit_cost_cny=Decimal(10),
    )

    from adwatch.inventory.service import InventoryService

    missing = InventoryService(database).list_missing_sku_costs()

    assert missing[0]["seller_sku"] == "SKU-A"
    assert missing[0]["order_count"] == 1
    assert missing[0]["first_missing_date"] == "2026-08-01"

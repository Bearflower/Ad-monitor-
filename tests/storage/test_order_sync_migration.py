from datetime import UTC, datetime

from adwatch.orders.models import PlatformOrderLine, PlatformSku
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


def test_order_sync_tables_are_created(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {
        "platform_order_lines",
        "platform_sku_mappings",
        "sku_cost_history",
        "order_sync_runs",
    } <= names


def test_sku_mapping_allows_same_seller_sku_for_multiple_models(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    with database.transaction() as connection:
        for model_id in ("1", "2"):
            connection.execute(
                """
                INSERT INTO platform_sku_mappings(
                    platform, store, item_id, model_id, seller_sku,
                    variation_name, product_name, inventory_units, observed_at
                ) VALUES ('shopee', 'store', 'item', ?, 'DUPLICATE-SKU',
                          ?, 'product', 1, '2026-08-04T09:00:00+08:00')
                """,
                (model_id, model_id),
            )

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM platform_sku_mappings"
        ).fetchone()[0]
    assert count == 2


def test_effective_sku_identity_does_not_split_legacy_and_real_same_product(
    tmp_path,
):
    database = Database(tmp_path / "same-product.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed_at = datetime(2026, 8, 10, 9, tzinfo=UTC)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'legacy-shop', 'shop')
            """
        )
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "legacy-shop", "product-old", "sku-old",
                "FOOT-SOAK-ONE", "1 bag", "Foot soak", 0, observed_at,
            ),
            PlatformSku(
                "shopee", "shop", "57861884313", "311033956020",
                "FOOT-SOAK-ONE", "1 bag", "Foot soak", 10, observed_at,
            ),
        )
    )
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "shop", "ORDER-1", "57861884313",
                "311033956020", "FOOT-SOAK-ONE", "1 bag", "Foot soak",
                1, 100, "THB", "completed", "delivered", "",
                observed_at, observed_at,
            ),
        )
    )

    with database.connect() as connection:
        identity = connection.execute(
            """
            SELECT sku_identity FROM effective_order_sku_lines
            WHERE order_id='ORDER-1'
            """
        ).fetchone()[0]

    assert identity == "FOOT-SOAK-ONE"

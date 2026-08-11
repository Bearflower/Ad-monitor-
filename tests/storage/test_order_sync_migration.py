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

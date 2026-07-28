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

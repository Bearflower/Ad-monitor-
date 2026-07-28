from adwatch.storage.db import Database


def test_inventory_migration_creates_required_tables(tmp_path):
    database = Database(tmp_path / "inventory.sqlite3")
    database.migrate()
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "purchase_receipts",
        "purchase_lines",
        "inventory_movements",
        "inventory_balances",
        "order_cost_snapshots",
    } <= tables

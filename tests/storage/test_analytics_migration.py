from adwatch.storage.db import Database


def test_v2_migration_adds_analysis_tables(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    with db.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "stores",
        "campaign_settings",
        "sku_mappings",
        "product_costs",
        "inventory_snapshots",
        "exchange_rates",
        "profit_results",
        "alerts",
        "recommendations",
        "system_settings",
        "circuit_state",
    } <= tables


def test_migration_creates_order_cost_lines_and_store_aliases(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        order_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(order_cost_lines)")
        }

    assert {"order_cost_lines", "store_aliases"} <= tables
    assert {
        "platform",
        "store",
        "order_id",
        "sku_id",
        "order_date",
        "quantity",
        "unit_cost_cny",
        "line_cost_cny",
        "source_file",
        "updated_at",
    } <= order_columns

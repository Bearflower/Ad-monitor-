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

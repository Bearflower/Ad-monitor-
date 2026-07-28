from adwatch.storage.db import Database


def test_operations_migration_creates_ledger_and_audit_tables(tmp_path):
    database = Database(tmp_path / "operations.sqlite3")
    database.migrate()

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {
        "expense_entries",
        "capital_entries",
        "withdrawal_entries",
        "ad_funding_entries",
        "ad_spend_entries",
        "review_order_costs",
        "cash_movements",
        "audit_events",
    } <= tables

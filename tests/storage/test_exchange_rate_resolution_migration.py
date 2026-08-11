from adwatch.storage.db import Database


def test_exchange_rates_store_resolution_provenance(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with database.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(exchange_rates)"
            )
        }

    assert {
        "execution_date",
        "source_date",
        "resolution_status",
        "resolved_at",
    } <= columns


def test_exchange_rate_resolution_migration_is_idempotent(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")

    database.migrate()
    database.migrate()

    with database.connect() as connection:
        versions = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 19"
        ).fetchone()[0]

    assert versions == 1

from adwatch.storage.db import Database


def test_database_migrates_required_tables(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    with db.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "schema_migrations",
        "daily_ad_metrics",
        "collection_runs",
        "quality_checks",
        "quarantined_records",
    } <= names

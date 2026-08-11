import pytest

import adwatch.storage.db as db_module
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


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        (
            (
                1,
                """
                CREATE TABLE half_applied(id INTEGER PRIMARY KEY);
                SELECT * FROM missing_table;
                """,
            ),
        ),
    )
    database = Database(tmp_path / "failed.sqlite3")

    with pytest.raises(Exception, match="missing_table"):
        database.migrate()

    with database.connect() as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name='half_applied'"
        ).fetchone()
        version = connection.execute(
            "SELECT version FROM schema_migrations WHERE version=1"
        ).fetchone()
    assert table is None
    assert version is None

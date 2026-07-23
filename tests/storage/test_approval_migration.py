from adwatch.storage.db import Database


def test_v3_migration_adds_approval_and_audit_tables(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"approvals", "execution_audits"} <= names

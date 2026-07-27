from adwatch.operations.backup import create_backup, verify_backup
from adwatch.storage.db import Database


def test_sqlite_backup_is_created_and_passes_integrity_check(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    destination = tmp_path / "backups" / "snapshot.sqlite3"

    create_backup(database, destination)

    assert destination.exists()
    assert verify_backup(destination) == "ok"

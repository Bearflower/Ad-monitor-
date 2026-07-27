from adwatch.operations.backup import create_backup, verify_backup
from adwatch.storage.db import Database


def test_sqlite_backup_is_created_and_passes_integrity_check(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    destination = tmp_path / "backups" / "snapshot.sqlite3"

    create_backup(database, destination)

    assert destination.exists()
    assert verify_backup(destination) == "ok"


def test_corrupt_backup_reports_invalid_instead_of_crashing(tmp_path):
    destination = tmp_path / "broken.sqlite3"
    destination.write_bytes(b"not sqlite")

    assert verify_backup(destination) == "invalid"

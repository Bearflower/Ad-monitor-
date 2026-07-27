import sqlite3
from pathlib import Path

from adwatch.storage.db import Database


def create_backup(database: Database, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = database.connect()
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return destination


def verify_backup(path: Path) -> str:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        return str(result)
    except sqlite3.DatabaseError:
        return "invalid"

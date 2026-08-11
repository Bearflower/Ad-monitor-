from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from adwatch.storage.migrations import MIGRATIONS


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        connection = self.connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in _migration_statements(sql):
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(version, applied_at)
                        VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                        """,
                        (version,),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            connection.close()


def _migration_statements(script: str) -> Iterator[str]:
    """Yield complete SQL statements so each migration stays transactional."""
    pending: list[str] = []
    for line in script.splitlines():
        pending.append(line)
        candidate = "\n".join(pending).strip()
        if candidate and sqlite3.complete_statement(candidate):
            yield candidate
            pending.clear()
    remainder = "\n".join(pending).strip()
    if remainder:
        raise ValueError("migration contains an incomplete SQL statement")

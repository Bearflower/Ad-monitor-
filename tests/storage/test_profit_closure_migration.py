import sqlite3

import pytest

from adwatch.storage.db import Database


def test_migration_29_creates_fulfillment_defaults_and_refund_inputs(tmp_path):
    database = Database(tmp_path / "closure.sqlite3")
    database.migrate()

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]

    assert {"store_fulfillment_history", "daily_refund_inputs"} <= tables
    assert version == 33


def test_daily_refund_input_rejects_negative_amount(tmp_path):
    database = Database(tmp_path / "closure-check.sqlite3")
    database.migrate()

    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_refund_inputs(
                platform, store, data_date, refund_amount_original,
                currency, status, source_type, source_ref,
                observed_on, closed_at, updated_at
            ) VALUES (
                'shopee', 'shop', '2026-08-01', '-1', 'THB',
                'observed', 'test', '', '2026-08-10', NULL,
                '2026-08-10T00:00:00Z'
            )
            """
        )


def test_migration_29_is_idempotent(tmp_path):
    database = Database(tmp_path / "closure-idempotent.sqlite3")
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=29"
        ).fetchone()[0] == 1

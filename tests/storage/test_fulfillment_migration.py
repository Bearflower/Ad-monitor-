import sqlite3

import pytest

from adwatch.storage.db import Database


def test_fulfillment_migration_creates_constrained_history_and_snapshots(
    tmp_path,
):
    database = Database(tmp_path / "fulfillment.sqlite3")
    database.migrate()
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO sku_fulfillment_history(
                    platform, store, seller_sku, effective_date,
                    mode, supply_status, note, created_at
                ) VALUES(
                    'shopee','shop','SKU-1','2026-07-01',
                    'unknown','available','','2026-07-01T00:00:00Z'
                )
                """
            )

    assert {
        "sku_fulfillment_history",
        "order_fulfillment_snapshots",
    } <= tables

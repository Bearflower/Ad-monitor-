import sqlite3

import pytest

from adwatch.storage.db import Database


def test_commerce_fact_tables_and_run_columns_are_created(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        run_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(order_sync_runs)"
            )
        }

    assert {
        "platform_refund_observations",
        "platform_logistics_observations",
        "platform_inventory_snapshots",
    } <= names
    assert {
        "start_date",
        "end_date",
        "refund_count",
        "logistics_count",
        "inventory_snapshot_count",
    } <= run_columns


def test_commerce_facts_are_unique_per_observation_day(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    values = (
        "shopee",
        "shop",
        "ORDER-1",
        "2026-08-04",
        "2026-08-01",
        "returned",
        None,
        "THB",
        "2026-08-04T09:00:00+00:00",
        "2026-08-04T09:00:00+00:00",
        "shopee_order_card",
    )

    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_refund_observations(
                platform, store, order_id, observed_on, order_date,
                refund_status, amount_original, currency,
                source_updated_at, observed_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO platform_refund_observations(
                    platform, store, order_id, observed_on, order_date,
                    refund_status, amount_original, currency,
                    source_updated_at, observed_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version, COUNT(*) FROM schema_migrations "
            "WHERE version=21 GROUP BY version"
        ).fetchall()
    assert [tuple(row) for row in versions] == [(21, 1)]

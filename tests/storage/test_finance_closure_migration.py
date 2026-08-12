import sqlite3

import pytest

import adwatch.storage.db as db_module
from adwatch.storage.db import Database
from adwatch.storage.migrations import MIGRATIONS


def test_migration_25_creates_finance_closure_schema(tmp_path):
    database = Database(tmp_path / "finance.sqlite3")
    database.migrate()

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            table: {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for table in (
                "evidence_documents",
                "financial_adjustments",
                "balance_snapshots",
                "balance_reconciliations",
                "inventory_movements",
                "profit_payments",
                "capital_entries",
                "withdrawal_entries",
                "ad_funding_entries",
                "review_order_costs",
                "purchase_receipts",
            )
        }
        latest = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]

    assert {
        "evidence_documents",
        "financial_adjustments",
        "balance_snapshots",
        "balance_reconciliations",
    } <= tables
    assert {
        "object_type",
        "object_id",
        "display_name",
        "stored_name",
        "media_type",
        "size_bytes",
        "sha256",
        "created_by",
    } <= columns["evidence_documents"]
    assert {"adjustment_type", "reversal_of", "created_by"} <= columns[
        "financial_adjustments"
    ]
    assert {"account_type", "snapshot_date", "amount_cny", "created_by"} <= (
        columns["balance_snapshots"]
    )
    assert {
        "ledger_cash_cny",
        "cash_observed_cny",
        "platform_observed_cny",
        "bank_observed_cny",
        "resolution_adjustment_id",
    } <= columns["balance_reconciliations"]
    assert {"actor", "reason", "reversal_of"} <= columns[
        "inventory_movements"
    ]
    assert {"created_by", "reversal_reason", "reversal_of"} <= columns[
        "profit_payments"
    ]
    for table in (
        "capital_entries",
        "withdrawal_entries",
        "ad_funding_entries",
        "review_order_costs",
        "purchase_receipts",
    ):
        assert {"created_by", "reversal_reason"} <= columns[table]
    assert latest == 39


def test_migration_25_backfills_legacy_actors_without_changing_facts(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "legacy-finance.sqlite3")
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        [migration for migration in MIGRATIONS if migration[0] <= 24],
    )
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO inventory_movements VALUES(
              'move-1','SKU-1','purchase_in',2,'2026-07-01',
              'purchase','PO-1','','2026-07-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO profit_share_agreements VALUES(
              'agreement','2026-01-01',NULL,1,'{"yl":"1"}',
              'yl','2026-01-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO profit_periods VALUES(
              'period','2026-07-01','2026-07-31','agreement','100',
              'confirmed',NULL,'yl','2026-08-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO profit_payments VALUES(
              'payment','period','yl','50','2026-08-01','paid','legacy',
              '2026-08-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO capital_entries VALUES(
              'capital','yl','paid_in','100','CNY','1','100',
              '2026-07-01','confirmed','2026-07-01T00:00:00Z')
            """
        )

    monkeypatch.setattr(db_module, "MIGRATIONS", MIGRATIONS)
    database.migrate()

    with database.connect() as connection:
        movement = connection.execute(
            """
            SELECT quantity_delta, actor, reason, reversal_of
            FROM inventory_movements WHERE id='move-1'
            """
        ).fetchone()
        payment = connection.execute(
            """
            SELECT amount_cny, status, created_by, reversal_reason, reversal_of
            FROM profit_payments WHERE id='payment'
            """
        ).fetchone()
        capital = connection.execute(
            """
            SELECT amount_cny, status, created_by, reversal_reason
            FROM capital_entries WHERE id='capital'
            """
        ).fetchone()

    assert tuple(movement) == (2, "system", "", None)
    assert tuple(payment) == ("50", "paid", "system", None, None)
    assert tuple(capital) == ("100", "confirmed", "system", None)


def test_migration_25_enforces_types_and_single_reversals(tmp_path):
    database = Database(tmp_path / "constraints.sqlite3")
    database.migrate()

    with database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO balance_snapshots(
                    id, account_type, account_name, snapshot_date,
                    amount_cny, source, created_by, created_at
                ) VALUES (
                    'bad','wallet','x','2026-08-04','1','manual','yl','now')
                """
            )
        connection.execute(
            """
            INSERT INTO financial_adjustments(
                id, adjustment_type, amount_cny, occurred_on, reason,
                status, created_by, created_at
            ) VALUES (
                'original','income','10','2026-08-04','补记',
                'confirmed','yl','now')
            """
        )
        connection.execute(
            """
            INSERT INTO financial_adjustments(
                id, adjustment_type, amount_cny, occurred_on, reason,
                status, reversal_of, created_by, created_at
            ) VALUES (
                'reversal-1','income','-10','2026-08-04','冲销',
                'reversed','original','yl','now')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO financial_adjustments(
                    id, adjustment_type, amount_cny, occurred_on, reason,
                    status, reversal_of, created_by, created_at
                ) VALUES (
                    'reversal-2','income','-10','2026-08-04','重复',
                    'reversed','original','yl','now')
                """
            )


def test_migration_25_rolls_back_all_statements_on_failure(tmp_path, monkeypatch):
    database = Database(tmp_path / "atomic.sqlite3")
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        [migration for migration in MIGRATIONS if migration[0] <= 24],
    )
    database.migrate()
    bad_migration = (
        25,
        """
        CREATE TABLE should_roll_back(id TEXT PRIMARY KEY);
        ALTER TABLE missing_table ADD COLUMN impossible TEXT;
        """,
    )
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        [migration for migration in MIGRATIONS if migration[0] <= 24]
        + [bad_migration],
    )

    with pytest.raises(sqlite3.OperationalError):
        database.migrate()

    with database.connect() as connection:
        table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='should_roll_back'
            """
        ).fetchone()
        latest = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
    assert table is None
    assert latest == 24

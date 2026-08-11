import adwatch.storage.db as db_module
from adwatch.storage.db import Database
from adwatch.storage.migrations import MIGRATIONS


def test_migration_24_creates_profit_lineage_and_recalculation_audit(tmp_path):
    database = Database(tmp_path / "lineage.sqlite3")
    database.migrate()

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        cost_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(product_costs)")
        }
        lineage_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(profit_input_lineage)"
            )
        }
        versions = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            )
        }

    assert {
        "profit_input_lineage",
        "analysis_recalculation_runs",
    } <= tables
    assert {"refund_data_status", "input_source_name"} <= cost_columns
    assert {
        "platform",
        "store",
        "account_id",
        "campaign_id",
        "sku_id",
        "data_date",
        "input_name",
        "value_text",
        "status",
        "source_type",
        "source_date",
        "age_days",
        "source_ref",
        "detail_code",
    } <= lineage_columns
    assert 24 in versions


def test_migration_24_keeps_legacy_zero_refund_pending(tmp_path, monkeypatch):
    database = Database(tmp_path / "legacy.sqlite3")
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        [migration for migration in MIGRATIONS if migration[0] <= 23],
    )
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO product_costs(
                sku_id, effective_date, product_cost, commission_rate,
                seller_shipping, coupons, allocated_fixed_cost, refund_amount
            ) VALUES ('SKU', '2026-07-01', '10', '0.08', '0', '0', '0', '0')
            """
        )

    monkeypatch.setattr(db_module, "MIGRATIONS", MIGRATIONS)
    database.migrate()

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT refund_amount, refund_data_status, input_source_name
            FROM product_costs WHERE sku_id='SKU'
            """
        ).fetchone()
    assert tuple(row) == ("0", "pending_data", "")

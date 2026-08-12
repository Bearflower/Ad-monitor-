from adwatch.storage.db import Database


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_migration_40_adds_formula_parity_columns_and_monthly_rates(tmp_path):
    database = Database(tmp_path / "formula-parity.sqlite3")
    database.migrate()
    with database.connect() as connection:
        latest = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        funding = _columns(connection, "ad_funding_entries")
        capital = _columns(connection, "capital_entries")
        review = _columns(connection, "review_order_costs")
        receipts = _columns(connection, "purchase_receipts")
        lines = _columns(connection, "purchase_lines")
    assert latest == 42
    assert "monthly_exchange_rate_overrides" in tables
    assert {
        "credited_amount_original",
        "credited_amount_cny",
        "rate_date",
        "rate_source",
    } <= funding
    assert {
        "entry_name",
        "currency",
        "rate_to_cny",
        "rate_date",
        "rate_source",
        "note",
    } <= capital
    assert {
        "goods_cost_original",
        "service_fee_original",
        "currency",
        "rate_to_cny",
    } <= review
    assert {
        "currency",
        "rate_to_cny",
        "rate_date",
        "rate_source",
        "tracking_number",
        "arrival_status",
        "note",
    } <= receipts
    assert {
        "product_name",
        "unit_cost_original",
        "line_cost_original",
    } <= lines


def test_migration_40_requires_currency_and_rate_for_new_purchase_rows(tmp_path):
    database = Database(tmp_path / "fresh.sqlite3")
    database.migrate()
    with database.connect() as connection:
        info = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info(purchase_receipts)")
        }
    assert info["currency"] == 1
    assert info["rate_to_cny"] == 1


def test_migration_41_creates_manual_income_entries(tmp_path):
    database = Database(tmp_path / "manual-income.sqlite3")
    database.migrate()
    with database.connect() as connection:
        latest = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        columns = _columns(connection, "manual_income_entries")
    assert latest == 42
    assert {
        "platform",
        "store",
        "order_id",
        "occurred_on",
        "amount_original",
        "currency",
        "rate_to_cny",
        "amount_cny",
        "rate_date",
        "rate_source",
        "status",
        "note",
        "created_by",
    } <= columns

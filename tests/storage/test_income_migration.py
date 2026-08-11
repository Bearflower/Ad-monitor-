import sqlite3

import adwatch.storage.db as db_module
from adwatch.storage.db import Database
from adwatch.storage.migrations import MIGRATIONS


def test_migration_22_creates_income_facts_and_profit_lineage(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        profit_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(profit_results)"
            )
        }
        fact_columns = {
            table: {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for table in (
                "platform_settlement_summaries",
                "platform_fee_entries",
                "platform_ad_funding_observations",
            )
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]

    assert {
        "platform_fee_statements",
        "income_import_events",
        "platform_settlement_summaries",
        "platform_fee_entries",
        "platform_ad_funding_observations",
    } <= tables
    assert {
        "fee_effective_rate",
        "fee_period_start",
        "fee_period_end",
        "fee_selection",
        "fee_source_name",
        "fee_source_sha256",
    } <= profit_columns
    assert all(
        {"statement_id", "statement_version", "is_current"} <= columns
        for columns in fact_columns.values()
    )
    assert 22 in versions
    assert 23 in versions


def test_income_statement_source_hash_is_unique(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    values = (
        "statement-1",
        "shopee",
        "shop",
        "store-alias",
        "2026-07-20",
        "2026-07-26",
        "THB",
        "4473",
        "500",
        "379",
        "150",
        "32",
        "1061",
        "0.237201",
        "35",
        "147",
        "965",
        "818",
        "13",
        "0",
        "3364",
        "weekly_report.pdf",
        "a" * 64,
        1,
        1,
        "2026-08-04T09:00:00+08:00",
    )
    placeholders = ",".join("?" for _ in values)

    with database.transaction() as connection:
        connection.execute(
            f"INSERT INTO platform_fee_statements VALUES ({placeholders})",
            values,
        )
        try:
            connection.execute(
                f"INSERT INTO platform_fee_statements VALUES ({placeholders})",
                ("statement-2", *values[1:]),
            )
        except sqlite3.IntegrityError as error:
            assert "UNIQUE" in str(error)
        else:
            raise AssertionError("expected duplicate source SHA to fail")


def test_migration_23_backfills_only_latest_revision_as_current(
    tmp_path,
    monkeypatch,
):
    database = Database(tmp_path / "income-v22.sqlite3")
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        [migration for migration in MIGRATIONS if migration[0] <= 22],
    )
    database.migrate()

    def statement_values(statement_id, source_sha, version, is_current):
        return (
            statement_id,
            "shopee",
            "shop",
            "store-alias",
            "2026-07-20",
            "2026-07-26",
            "THB",
            "4473",
            "500",
            "379",
            "150",
            "32",
            "1061",
            "0.237201",
            "35",
            "147",
            "965",
            "818",
            "13",
            "0",
            "3364",
            f"weekly_report_v{version}.pdf",
            source_sha,
            version,
            is_current,
            f"2026-08-04T0{version}:00:00+08:00",
        )

    with database.transaction() as connection:
        for statement_id, source_sha, version, current in (
            ("statement-1", "a" * 64, 1, 0),
            ("statement-2", "b" * 64, 2, 1),
        ):
            values = statement_values(
                statement_id,
                source_sha,
                version,
                current,
            )
            connection.execute(
                "INSERT INTO platform_fee_statements VALUES "
                f"({','.join('?' for _ in values)})",
                values,
            )
            prefix = f"income:{source_sha}"
            connection.execute(
                """
                INSERT INTO platform_settlement_summaries VALUES (
                    ?, 'shopee', 'shop', '2026-07-20', '2026-07-26',
                    '4473', '3364', 'THB', 'settlement_only', ?,
                    'shopee_income_pdf', '2026-08-04T00:00:00Z'
                )
                """,
                (f"{prefix}:settlement", source_sha),
            )
            connection.execute(
                """
                INSERT INTO platform_fee_entries VALUES (
                    ?, 'shopee', 'shop', '2026-07-20', '2026-07-26',
                    'commission_fee', '-500', 'THB', ?,
                    'shopee_income_pdf', '2026-08-04T00:00:00Z'
                )
                """,
                (f"{prefix}:fee:commission", source_sha),
            )
            connection.execute(
                """
                INSERT INTO platform_ad_funding_observations VALUES (
                    ?, 'shopee', 'shop', '2026-07-26', 'topup', '13',
                    'THB', 'settlement_deduction', ?,
                    'shopee_income_pdf', '2026-08-04T00:00:00Z'
                )
                """,
                (f"{prefix}:funding", source_sha),
            )

    monkeypatch.setattr(db_module, "MIGRATIONS", MIGRATIONS)
    database.migrate()

    with database.connect() as connection:
        migrated = {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"""
                    SELECT source_sha256, statement_version, is_current
                    FROM {table} ORDER BY statement_version
                    """
                ).fetchall()
            ]
            for table in (
                "platform_settlement_summaries",
                "platform_fee_entries",
                "platform_ad_funding_observations",
            )
        }
    expected = [("a" * 64, 1, 0), ("b" * 64, 2, 1)]
    assert all(rows == expected for rows in migrated.values())

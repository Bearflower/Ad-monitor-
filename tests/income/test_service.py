import hashlib
from dataclasses import replace
from decimal import Decimal

import pytest

from adwatch.income.models import FeeStatement
from adwatch.income.service import (
    IncomeImportService,
    IncomeScanError,
)
from adwatch.storage.db import Database


def _statement(path, *, source_store=None) -> FeeStatement:
    from tests.income.test_repository import _statement as base_statement

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    alias = source_store or path.parent.name
    return replace(
        base_statement(source_sha256=digest),
        store=alias,
        source_store=alias,
        source_name=path.name,
    )


def test_scan_imports_known_once_and_quarantines_unknown_store(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'known-alias', 'shop')
            """
        )
    root = tmp_path / "downloads"
    known = root / "known-alias" / "weekly_report_known.pdf"
    unknown = root / "unknown-alias" / "weekly_report_unknown.pdf"
    known.parent.mkdir(parents=True)
    unknown.parent.mkdir(parents=True)
    known.write_bytes(b"known")
    unknown.write_bytes(b"unknown")
    service = IncomeImportService(
        database,
        scan_root=root,
        parsers={"shopee": _statement},
    )

    first = service.scan()
    second = service.scan()

    assert (
        first.discovered,
        first.imported,
        first.skipped,
        first.quarantined,
        first.failed,
    ) == (2, 1, 0, 1, 0)
    assert (second.discovered, second.skipped) == (2, 2)
    with database.connect() as connection:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "platform_fee_statements",
                "income_import_events",
                "platform_settlement_summaries",
                "platform_fee_entries",
                "platform_ad_funding_observations",
            )
        )
        metrics = connection.execute(
            "SELECT COUNT(*) FROM daily_ad_metrics"
        ).fetchone()[0]
    assert counts == (1, 2, 1, 9, 1)
    assert metrics == 0


def test_import_failure_rolls_back_statement_and_facts_but_records_event(
    tmp_path,
):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'known-alias', 'shop')
            """
        )
    path = tmp_path / "known-alias" / "weekly_report_broken.pdf"
    path.parent.mkdir()
    path.write_bytes(b"broken")

    class BrokenCommerceRepository:
        def upsert_income_statement_facts(self, connection, statement):
            raise RuntimeError("database fact write failed")

    service = IncomeImportService(
        database,
        scan_root=tmp_path,
        parsers={"shopee": _statement},
        commerce_repository=BrokenCommerceRepository(),
    )

    result = service.import_file(path, platform="shopee")

    assert result.status == "failed"
    assert result.reason_code == "fact_write_failed"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM platform_fee_statements"
        ).fetchone()[0] == 0
        event = connection.execute(
            "SELECT status, reason_code FROM income_import_events"
        ).fetchone()
    assert tuple(event) == ("failed", "fact_write_failed")


def test_fee_statement_test_helper_keeps_exact_reconciliation(tmp_path):
    path = tmp_path / "alias" / "weekly_report.pdf"
    path.parent.mkdir()
    path.write_bytes(b"statement")

    statement = _statement(path)

    assert statement.sales_fee_total == Decimal(1061)


def test_manual_retry_rechecks_terminal_file_after_store_mapping_is_fixed(
    tmp_path,
):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    path = tmp_path / "new-alias" / "weekly_report.pdf"
    path.parent.mkdir()
    path.write_bytes(b"statement")
    service = IncomeImportService(
        database,
        scan_root=tmp_path,
        parsers={"shopee": _statement},
    )

    assert service.import_file(path, platform="shopee").status == "quarantined"
    assert service.import_file(path, platform="shopee").status == "skipped"
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'new-alias', 'shop')
            """
        )

    retried = service.import_file(
        path,
        platform="shopee",
        retry_terminal=True,
    )

    assert retried.status == "imported"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT status, reason_code FROM income_import_events"
        ).fetchone()
    assert tuple(event) == ("imported", "")


def test_scan_converts_directory_io_failure_to_safe_reason(tmp_path):
    class BrokenScanRoot:
        def is_dir(self):
            return True

        def rglob(self, pattern):
            raise OSError("permission denied: /Users/private/income")

    service = IncomeImportService(
        Database(tmp_path / "income.sqlite3"),
        scan_root=BrokenScanRoot(),
        parsers={"shopee": _statement},
    )

    with pytest.raises(IncomeScanError) as captured:
        service.scan()

    assert str(captured.value) == "income_scan_io_failed"
    assert "/Users/" not in str(captured.value)


def test_manual_failed_retry_refreshes_terminal_audit_status(tmp_path):
    database = Database(tmp_path / "income.sqlite3")
    database.migrate()
    path = tmp_path / "unknown-alias" / "weekly_report.pdf"
    path.parent.mkdir()
    path.write_bytes(b"statement")
    service = IncomeImportService(
        database,
        scan_root=tmp_path,
        parsers={"shopee": _statement},
    )
    assert service.import_file(path, platform="shopee").status == "quarantined"

    def broken_parser(_path):
        from adwatch.income.shopee_pdf import IncomeParseError

        raise IncomeParseError("unsafe parser detail")

    service.parsers["shopee"] = broken_parser
    retried = service.import_file(
        path,
        platform="shopee",
        retry_terminal=True,
    )

    assert retried.status == "failed"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT status, reason_code FROM income_import_events"
        ).fetchone()
    assert tuple(event) == ("failed", "parse_failed")

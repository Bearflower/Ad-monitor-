from pathlib import Path

import adwatch.cli
from adwatch.cli import main
from adwatch.config import Settings
from adwatch.income.repository import FeeStatementRepository
from adwatch.income.service import ImportResult, ScanResult
from adwatch.storage.db import Database
from tests.income.test_repository import _statement


def test_income_import_and_scan_outputs_safe_fixed_summary(
    tmp_path,
    monkeypatch,
    capsys,
):
    settings = Settings(data_dir=tmp_path, income_scan_root=tmp_path / "scan")
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    source = tmp_path / "private" / "weekly_report.pdf"
    source.parent.mkdir()
    source.write_bytes(b"pdf")

    class FakeService:
        def __init__(self, database, *, scan_root):
            assert scan_root == settings.income_scan_root

        def import_file(self, path, *, platform, retry_terminal=False):
            assert path == source
            assert platform == "shopee"
            assert retry_terminal is True
            return ImportResult("imported", statement=_statement())

        def scan(self):
            return ScanResult(discovered=2, imported=1, skipped=1)

    monkeypatch.setattr(adwatch.cli, "IncomeImportService", FakeService)

    assert main(
        ["income", "import", "--platform", "shopee", "--file", str(source)]
    ) == 0
    assert main(["income", "scan"]) == 0

    output = capsys.readouterr().out
    assert "income_import=imported platform=shopee store=shop" in output
    assert "rate=23.7201% source=weekly_report.pdf" in output
    assert "income_scan=ok discovered=2 imported=1" in output
    assert str(source.parent) not in output


def test_income_list_shows_current_versions_without_full_path(
    tmp_path,
    monkeypatch,
    capsys,
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    database = Database(settings.database_path)
    database.migrate()
    FeeStatementRepository(database).save(_statement())

    assert main(["income", "list"]) == 0

    output = capsys.readouterr().out
    assert "shopee shop 2026-07-20..2026-07-26" in output
    assert "rate=23.7201% source=weekly_report.pdf sha=aaaaaaaaaaaa" in output
    assert "/Users/" not in output


def test_income_tiktok_import_is_pending_external(
    tmp_path,
    monkeypatch,
    capsys,
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)

    assert main(
        [
            "income",
            "import",
            "--platform",
            "tiktok",
            "--file",
            str(Path("weekly_report.pdf")),
        ]
    ) == 2

    assert "income_import=pending_external platform=tiktok" in capsys.readouterr().out

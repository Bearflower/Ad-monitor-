import csv
from datetime import date
from decimal import Decimal

from adwatch.cli import main
from adwatch.config import Settings
from adwatch.domain import DailyAdMetric, Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


def _settings(tmp_path):
    return Settings(data_dir=tmp_path)


def _insert_metric(database):
    database.migrate()
    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, data_date):
            return [
                DailyAdMetric(
                data_date=date(2026, 7, 23),
                platform=Platform.SHOPEE,
                store="虾皮泰国",
                account_id="account-1",
                campaign_id="campaign-1",
                sku_id="__ALL__",
                currency="THB",
                spend=Decimal("100"),
                attributed_gmv=Decimal("300"),
                orders=5,
                source="test",
                )
            ]

    PipelineRunner(database).run(Collector(), date(2026, 7, 23))


def test_business_export_template_cli(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    _insert_metric(database)
    output = tmp_path / "inputs.csv"
    monkeypatch.setattr(Settings, "from_env", lambda: settings)

    result = main(
        [
            "business",
            "export-template",
            "--from",
            "2026-07-23",
            "--to",
            "2026-07-23",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert "Exported 1 business input rows" in capsys.readouterr().out
    with output.open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["campaign_id"] == "campaign-1"


def test_business_import_cli_reports_validation_error(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    source = tmp_path / "bad.csv"
    source.write_text("data_date,platform\n2026-07-23,shopee\n", encoding="utf-8")
    monkeypatch.setattr(Settings, "from_env", lambda: settings)

    assert main(["business", "import", "--file", str(source)]) == 2
    assert "Business input rejected:" in capsys.readouterr().out

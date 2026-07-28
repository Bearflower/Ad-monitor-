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
                spend=Decimal(100),
                attributed_gmv=Decimal(300),
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
        assert next(iter(csv.DictReader(handle)))["campaign_id"] == "campaign-1"


def test_business_import_cli_reports_validation_error(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    source = tmp_path / "bad.csv"
    source.write_text("data_date,platform\n2026-07-23,shopee\n", encoding="utf-8")
    monkeypatch.setattr(Settings, "from_env", lambda: settings)

    assert main(["business", "import", "--file", str(source)]) == 2
    assert "Business input rejected:" in capsys.readouterr().out


def test_business_import_minimal_cli(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    _insert_metric(database)
    source = tmp_path / "minimal.csv"
    source.write_text(
        "data_date,total_product_cost,refund_amount\n"
        "2026-07-23,120,0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "from_env", lambda: settings)

    assert main(["business", "import-minimal", "--file", str(source)]) == 0
    assert "Imported 1 minimal business input rows" in capsys.readouterr().out


def test_business_order_cost_commands(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    _insert_metric(database)
    source = tmp_path / "orders.csv"
    source.write_text(
        "日期,平台,店铺,订单号,SKU,数量,单件成本_人民币\n"
        "20260723,shopee,no4kud44da,ORDER-1,1 bag,1,5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "from_env", lambda: settings)

    assert main(["business", "import-orders", "--file", str(source)]) == 0
    assert (
        "Imported order costs: read=1 inserted=1 updated=0 deduplicated=0"
        in capsys.readouterr().out
    )
    assert main(
        [
            "business",
            "map-store",
            "--platform",
            "shopee",
            "--source",
            "no4kud44da",
            "--target",
            "虾皮泰国",
        ]
    ) == 0
    assert "no4kud44da -> 虾皮泰国" in capsys.readouterr().out
    assert main(
        [
            "business",
            "order-summary",
            "--from",
            "2026-07-23",
            "--to",
            "2026-07-23",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "2026-07-23 shopee no4kud44da -> 虾皮泰国" in output
    assert "orders=1 units=1 total_cost_cny=5.00" in output


def test_business_pending_sku_cost_commands(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    output = tmp_path / "pending.xlsx"

    assert main(
        [
            "business",
            "export-pending-sku-costs",
            "--output",
            str(output),
        ]
    ) == 0
    assert output.is_file()
    assert "Exported 0 pending SKU costs" in capsys.readouterr().out

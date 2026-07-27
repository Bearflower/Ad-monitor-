from decimal import Decimal

import adwatch.cli
from adwatch.cli import main
from adwatch.config import Settings
from adwatch.domain import DailyAdMetric


def test_daily_run_creates_all_local_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    assert (
        main(["run", "daily", "--mode", "mock", "--date", "2026-07-22"])
        == 0
    )
    assert (tmp_path / "adwatch.sqlite3").exists()
    assert (tmp_path / "reports" / "quality-2026-07-22.json").exists()
    assert (tmp_path / "reports" / "daily-2026-07-22.md").exists()


def test_daily_ziniao_run_uses_real_collectors_and_marks_real_report(
    tmp_path, monkeypatch, capsys
):
    class RealCollector:
        source = "ziniao"

        def __init__(self, settings, platform):
            self.platform = platform

        def collect(self, data_date):
            if self.platform.value == "tiktok":
                return []
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="虾皮泰国",
                    account_id="222",
                    campaign_id="Shop GMV Max",
                    sku_id="SKU-1",
                    data_date=data_date,
                    currency="THB",
                    spend=Decimal("10"),
                    attributed_gmv=Decimal("30"),
                    orders=1,
                    source=self.source,
                )
            ]

    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "ZiniaoCollector", RealCollector)

    assert (
        main(["run", "daily", "--mode", "ziniao", "--date", "2026-07-22"])
        == 0
    )

    report = (tmp_path / "reports" / "daily-2026-07-22.md").read_text()
    assert "【真实数据】" in report
    assert "【模拟数据】" not in report
    assert "daily_run=ok metrics=1" in capsys.readouterr().out


def test_daily_run_keeps_shopee_data_when_tiktok_collection_fails(
    tmp_path, monkeypatch, capsys
):
    class PartiallyFailingCollector:
        source = "ziniao"

        def __init__(self, settings, platform):
            self.platform = platform

        def collect(self, data_date):
            if self.platform.value == "tiktok":
                raise RuntimeError("TikTok page changed")
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="虾皮泰国",
                    account_id="222",
                    campaign_id="Shop GMV Max",
                    sku_id="__ALL__",
                    data_date=data_date,
                    currency="THB",
                    spend=Decimal("10"),
                    attributed_gmv=Decimal("30"),
                    orders=1,
                    source=self.source,
                )
            ]

    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        adwatch.cli, "ZiniaoCollector", PartiallyFailingCollector
    )

    result = main(
        ["run", "daily", "--mode", "ziniao", "--date", "2026-07-22"]
    )

    assert result == 2
    output = capsys.readouterr().out
    assert "tiktok collection failed: RuntimeError" in output
    assert "daily_run=partial metrics=1" in output
    database = adwatch.cli.Database(settings.database_path)
    with database.connect() as connection:
        platforms = connection.execute(
            "SELECT DISTINCT platform FROM daily_ad_metrics"
        ).fetchall()
    assert [row[0] for row in platforms] == ["shopee"]

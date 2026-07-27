from datetime import date

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.reporting.markdown import (
    render_daily_markdown,
    render_monthly_markdown,
)
from adwatch.reporting.read_model import ReportReadModel
from adwatch.storage.db import Database


def test_daily_report_contains_required_sections(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.TIKTOK), data_date)
    runner.run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    service.run(data_date)

    report = render_daily_markdown(
        ReportReadModel(database).daily(data_date), simulated=True
    )

    for heading in ("【TikTok】", "【Shopee】", "【异常告警】", "【TOP3/BOTTOM3】"):
        assert heading in report
    assert "模拟数据" in report


def test_daily_report_labels_profit_as_pending_without_business_data(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    AnalysisService(database).run(data_date)

    report = render_daily_markdown(
        ReportReadModel(database).daily(data_date), simulated=False
    )

    assert "利润待补数据" in report
    assert "净利润(CNY) 0.00" not in report


def test_monthly_report_aggregates_available_daily_snapshots(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    snapshot = ReportReadModel(database).daily(data_date)

    report = render_monthly_markdown([snapshot], month="2026-07")

    assert "# 广告月报 2026-07" in report
    assert "Shopee" in report

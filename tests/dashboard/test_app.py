from datetime import date

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.dashboard.app import render_dashboard
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


def test_dashboard_is_read_only_responsive_and_escapes_data(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.TIKTOK), data_date)
    runner.run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    service.run(data_date)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE daily_ad_metrics SET store='<script>alert(1)</script>' "
            "WHERE platform='tiktok'"
        )

    html = render_dashboard(database, data_date, simulated=True)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "模拟数据" in html
    assert "TikTok" in html and "Shopee" in html
    assert "净利润" in html and "策略建议" in html
    assert '<meta name="viewport"' in html


def test_dashboard_filters_platform_and_exposes_filter_controls(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.TIKTOK), data_date)
    runner.run(MockCollector(Platform.SHOPEE), data_date)

    html = render_dashboard(
        database,
        data_date,
        simulated=True,
        platform="shopee",
    )

    assert 'name="platform"' in html
    assert "Shopee" in html
    assert 'class="eyebrow">Tiktok' not in html


def test_dashboard_renders_trends_quality_and_execution(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)

    page = render_dashboard(database, data_date, simulated=False)

    assert "7 天趋势" in page
    assert "14 天趋势" in page
    assert "30 天趋势" in page
    assert "采集运行质量" in page
    assert "审批与执行状态" in page

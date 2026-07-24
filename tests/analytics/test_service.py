from datetime import date

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


def test_analysis_is_idempotent_for_one_date(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.TIKTOK), data_date)
    runner.run(MockCollector(Platform.SHOPEE), data_date)

    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    first = service.run(data_date)
    second = service.run(data_date)

    assert first.metrics_processed == 8
    assert second.metrics_processed == 8
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM profit_results WHERE data_date='2026-07-22'"
        ).fetchone()[0]
    assert count == 8


def test_analysis_rebuilds_alerts_after_snapshot_replacement(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    service = AnalysisService(database)

    runner.run(MockCollector(Platform.SHOPEE), data_date)
    service.run(data_date)

    class OneMetricCollector(MockCollector):
        def collect(self, day):
            return super().collect(day)[:1]

    runner.run(OneMetricCollector(Platform.SHOPEE), data_date)
    service.run(data_date)

    with database.connect() as connection:
        alerts = connection.execute(
            "SELECT COUNT(*) FROM alerts WHERE data_date='2026-07-23'"
        ).fetchone()[0]
    assert alerts == 1

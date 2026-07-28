from datetime import date
from decimal import Decimal

from adwatch.collectors.mock import MockCollector
from adwatch.domain import DailyAdMetric, Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


def test_replaying_same_day_is_idempotent(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    runner = PipelineRunner(db)
    collector = MockCollector(Platform.TIKTOK)

    first = runner.run(collector, date(2026, 7, 22))
    second = runner.run(collector, date(2026, 7, 22))

    assert first.accepted > 0
    assert second.accepted == first.accepted
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM daily_ad_metrics"
        ).fetchone()[0]
    assert count == first.accepted


def test_new_collection_replaces_platform_day_snapshot(tmp_path):
    class SnapshotCollector:
        source = "ziniao"
        platform = Platform.SHOPEE

        def __init__(self, sku_ids):
            self.sku_ids = sku_ids

        def collect(self, data_date):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="虾皮泰国",
                    account_id="account",
                    campaign_id="Shop GMV Max",
                    sku_id=sku_id,
                    data_date=data_date,
                    currency="THB",
                    spend=Decimal(10),
                    attributed_gmv=Decimal(30),
                    orders=1,
                    source="ziniao-cli",
                )
                for sku_id in self.sku_ids
            ]

    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    runner = PipelineRunner(db)
    day = date(2026, 7, 23)

    runner.run(SnapshotCollector(["old-1", "old-2"]), day)
    runner.run(SnapshotCollector(["__ALL__"]), day)

    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT sku_id FROM daily_ad_metrics
            WHERE platform='shopee' AND data_date='2026-07-23'
            """
        ).fetchall()
    assert [row["sku_id"] for row in rows] == ["__ALL__"]

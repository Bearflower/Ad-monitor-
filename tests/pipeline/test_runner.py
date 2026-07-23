from datetime import date

from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform
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

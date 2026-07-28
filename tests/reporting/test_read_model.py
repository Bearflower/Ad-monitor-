from datetime import date

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.reporting.read_model import ReportReadModel
from adwatch.storage.db import Database


def test_dashboard_read_model_includes_trends_and_operations(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.SHOPEE), date(2026, 7, 22))
    runner.run(MockCollector(Platform.SHOPEE), date(2026, 7, 23))
    with database.transaction() as connection:
        recommendation_id = connection.execute(
            """
            INSERT INTO recommendations(
                rule_code, platform, campaign_id, sku_id, data_date,
                action, reason, requires_approval
            ) VALUES (
                'test', 'shopee', 'campaign', 'SKU', '2026-07-23',
                'pause', 'test', 1
            )
            """
        ).lastrowid
        connection.execute(
            """
            INSERT INTO approvals(
                id, recommendation_id, status, requested_at, expires_at,
                decision_token_hash
            ) VALUES (
                'approval-1', ?, 'approved',
                '2026-07-23T00:00:00+00:00',
                '2026-07-24T00:00:00+00:00', 'hash'
            )
            """,
            (recommendation_id,),
        )
        connection.execute(
            """
            INSERT INTO execution_audits(
                id, approval_id, action, status, idempotency_key, created_at
            ) VALUES (
                'audit-1', 'approval-1', 'pause', 'succeeded',
                'key-1', '2026-07-23T01:00:00+00:00'
            )
            """
        )

    snapshot = ReportReadModel(database).dashboard(date(2026, 7, 23))

    assert set(snapshot.trends) == {7, 14, 30}
    assert len(snapshot.trends[7]) == 2
    assert snapshot.collection_runs
    assert snapshot.approval_counts["approved"] == 1
    assert snapshot.execution_counts["succeeded"] == 1


def test_daily_read_model_includes_platform_profit_breakdown(tmp_path):
    data_date = date(2026, 7, 22)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
    analysis.run(data_date)

    platform = ReportReadModel(database).daily(data_date).platforms[0]

    assert platform.attributed_sales_cny is not None
    assert platform.platform_fee_cny is not None
    assert platform.ad_spend_cny is not None
    assert platform.sku_and_other_cost_cny is not None
    assert (
        platform.attributed_sales_cny
        - platform.platform_fee_cny
        - platform.ad_spend_cny
        - platform.sku_and_other_cost_cny
        == platform.net_profit
    )

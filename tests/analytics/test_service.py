from datetime import date
from decimal import Decimal

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import DailyAdMetric, Platform
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


def test_missing_business_inputs_are_pending_data_not_write_circuit(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)

    summary = AnalysisService(database).run(data_date)

    assert summary.pending_data == 4
    assert summary.circuit_open is False
    with database.connect() as connection:
        severities = connection.execute(
            "SELECT DISTINCT severity FROM alerts WHERE data_date=?",
            (data_date.isoformat(),),
        ).fetchall()
    assert [row[0] for row in severities] == ["info"]


def test_analysis_exposes_capability_statuses_when_costs_are_missing(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)

    summary = AnalysisService(database).run(data_date)

    assert summary.capabilities == {
        "platform_metrics": "ready",
        "estimated_profit": "pending_data",
        "verified_profit": "pending_data",
        "inventory_safe_strategy": "pending_data",
    }


def test_seeded_complete_inputs_expose_all_analysis_capabilities(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)

    summary = service.run(data_date)

    assert summary.capabilities == {
        "platform_metrics": "ready",
        "estimated_profit": "ready",
        "verified_profit": "ready",
        "inventory_safe_strategy": "ready",
    }


def test_analysis_wires_historical_spend_and_roas_anomalies(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def __init__(self, spend, gmv):
            self.spend = Decimal(spend)
            self.gmv = Decimal(gmv)

        def collect(self, data_date):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="store",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=data_date,
                    currency="THB",
                    spend=self.spend,
                    attributed_gmv=self.gmv,
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector("100", "200"), date(2026, 7, 22))
    PipelineRunner(database).run(Collector("150", "150"), date(2026, 7, 23))

    AnalysisService(database).run(date(2026, 7, 23))

    with database.connect() as connection:
        codes = {
            row[0]
            for row in connection.execute(
                "SELECT rule_code FROM alerts WHERE data_date='2026-07-23'"
            )
        }
    assert {"spend_jump", "roas_drop"} <= codes


def test_three_recent_webdriver_failures_open_circuit(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class FailingCollector:
        source = "ziniao"
        platform = Platform.TIKTOK

        def collect(self, data_date):
            raise RuntimeError("Bridge unavailable")

    for _ in range(3):
        try:
            PipelineRunner(database).run(
                FailingCollector(), date(2026, 7, 23)
            )
        except RuntimeError:
            pass

    summary = AnalysisService(database).run(date(2026, 7, 23))

    assert summary.circuit_open is True
    with database.connect() as connection:
        reasons = connection.execute(
            "SELECT reasons_json FROM circuit_state WHERE id=1"
        ).fetchone()[0]
    assert "webdriver_failure_limit" in reasons


def test_two_days_global_low_roas_open_circuit(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class LowCollector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, data_date):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="store",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=data_date,
                    currency="THB",
                    spend=Decimal("100"),
                    attributed_gmv=Decimal("100"),
                    orders=1,
                    source=self.source,
                )
            ]

    for day in (date(2026, 7, 22), date(2026, 7, 23)):
        PipelineRunner(database).run(LowCollector(), day)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO campaign_settings(
                platform, campaign_id, start_date, target_roas,
                current_budget, baseline_budget
            ) VALUES ('shopee', 'campaign', '2026-07-01', '2', '100', '100')
            """
        )

    summary = AnalysisService(database).run(date(2026, 7, 23))

    assert summary.circuit_open is True
    with database.connect() as connection:
        reasons = connection.execute(
            "SELECT reasons_json FROM circuit_state WHERE id=1"
        ).fetchone()[0]
    assert "global_low_roas" in reasons


def test_three_real_low_days_feed_pause_strategy(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class LowCollector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, data_date):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="store",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=data_date,
                    currency="THB",
                    spend=Decimal("100"),
                    attributed_gmv=Decimal("80"),
                    orders=1,
                    source=self.source,
                )
            ]

    for day in (
        date(2026, 7, 21),
        date(2026, 7, 22),
        date(2026, 7, 23),
    ):
        PipelineRunner(database).run(LowCollector(), day)
    service = AnalysisService(database)
    service.seed_mock_business_data(date(2026, 7, 23))
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE campaign_settings
            SET start_date='2026-07-01', target_roas='2'
            WHERE platform='shopee' AND campaign_id='campaign'
            """
        )

    service.run(date(2026, 7, 23))

    with database.connect() as connection:
        recommendations = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT action, store_id FROM recommendations
                WHERE data_date='2026-07-23'
                """
            )
        ]
    assert recommendations == [("pause", "account")]


def test_verified_retest_candidate_is_persisted_with_capped_amount(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO product_retest_candidates(
                platform, campaign_id, sku_id, available_test_budget, enabled
            ) VALUES ('shopee', 'shopee-campaign-1', 'SKU-001', '300', 1)
            """
        )
        connection.execute(
            """
            UPDATE campaign_settings
            SET current_budget='1000', baseline_budget='1000'
            WHERE platform='shopee' AND campaign_id='shopee-campaign-1'
            """
        )

    service.run(data_date)

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT action, amount FROM recommendations
            WHERE rule_code='allocate_product_retest'
            """
        ).fetchone()
    assert tuple(row) == ("allocate_retest", "200.00")

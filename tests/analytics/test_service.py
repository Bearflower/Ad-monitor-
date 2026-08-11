from dataclasses import replace
from datetime import date
from decimal import Decimal

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import DailyAdMetric, Platform
from adwatch.income.repository import FeeStatementRepository
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.analytics import AnalyticsRepository
from adwatch.storage.db import Database
from tests.income.test_repository import _statement


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


def test_analysis_uses_covering_income_rate_and_persists_lineage(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "income-rate.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    FeeStatementRepository(database).save(
        replace(
            _statement(),
            store="Shopee TH Store",
            source_store="store-alias",
        )
    )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]
    summary = service.run(data_date)

    assert Decimal(row["legacy_commission_rate"]) == Decimal("0.08")
    assert Decimal(row["resolved_commission_rate"]) == Decimal("0.237201")
    assert row["fee_selection"] == "covering_period"
    assert summary.profit_results == 4
    with database.connect() as connection:
        lineage = connection.execute(
            """
            SELECT DISTINCT fee_effective_rate, fee_period_start,
                            fee_period_end, fee_selection,
                            fee_source_name, fee_source_sha256
            FROM profit_results WHERE data_date=?
            """,
            (data_date.isoformat(),),
        ).fetchall()
    assert [tuple(item) for item in lineage] == [
        (
            "0.237201",
            "2026-07-20",
            "2026-07-26",
            "covering_period",
            "weekly_report.pdf",
            "a" * 64,
        )
    ]


def test_analysis_carries_income_rate_without_cross_platform_leak(tmp_path):
    data_date = date(2026, 7, 29)
    database = Database(tmp_path / "income-rate.sqlite3")
    database.migrate()
    runner = PipelineRunner(database)
    runner.run(MockCollector(Platform.SHOPEE), data_date)
    runner.run(MockCollector(Platform.TIKTOK), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    FeeStatementRepository(database).save(
        replace(
            _statement(),
            store="Shopee TH Store",
            source_store="store-alias",
        )
    )

    rows = AnalyticsRepository(database).load_analysis_rows(data_date)
    shopee = next(row for row in rows if row["platform"] == "shopee")
    tiktok = next(row for row in rows if row["platform"] == "tiktok")

    assert shopee["fee_selection"] == "carried_forward"
    assert Decimal(shopee["resolved_commission_rate"]) == Decimal("0.237201")
    assert tiktok["fee_selection"] == "legacy_cost_rate"
    assert Decimal(tiktok["resolved_commission_rate"]) == Decimal("0.08")


def test_analysis_keeps_missing_fee_rate_pending_instead_of_zero(tmp_path):
    data_date = date(2026, 7, 29)
    database = Database(tmp_path / "missing-rate.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute("DELETE FROM product_costs")

    rows = AnalyticsRepository(database).load_analysis_rows(data_date)

    summary = service.run(data_date)

    assert all(row["resolved_commission_rate"] is None for row in rows)
    assert all(row["fee_selection"] is None for row in rows)
    assert summary.profit_results == 0
    assert summary.pending_data == 4
    with database.connect() as connection:
        result_count = connection.execute(
            "SELECT COUNT(*) FROM profit_results WHERE data_date=?",
            (data_date.isoformat(),),
        ).fetchone()[0]
    assert result_count == 0


def test_analysis_does_not_use_income_rate_from_another_store(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "store-isolation.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    FeeStatementRepository(database).save(
        replace(
            _statement(),
            store="Another Shopee Store",
            source_store="another-alias",
        )
    )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]

    assert row["fee_selection"] == "legacy_cost_rate"
    assert Decimal(row["resolved_commission_rate"]) == Decimal("0.08")


def test_legacy_fee_rate_is_pending_for_sku_shared_across_scopes(tmp_path):
    shopee_date = date(2026, 7, 23)
    tiktok_date = date(2026, 7, 24)
    database = Database(tmp_path / "shared-sku.sqlite3")
    database.migrate()

    class SharedSkuCollector:
        source = "test"

        def __init__(self, platform, store, currency):
            self.platform = platform
            self.store = store
            self.currency = currency

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store=self.store,
                    account_id=f"{self.platform.value}-account",
                    campaign_id=f"{self.platform.value}-campaign",
                    sku_id="SHARED-SKU",
                    data_date=day,
                    currency=self.currency,
                    spend=Decimal(10),
                    attributed_gmv=Decimal(20),
                    orders=1,
                    source=self.source,
                )
            ]

    runner = PipelineRunner(database)
    runner.run(
        SharedSkuCollector(Platform.SHOPEE, "Shopee Store", "THB"),
        shopee_date,
    )
    runner.run(
        SharedSkuCollector(Platform.TIKTOK, "TikTok Store", "MYR"),
        tiktok_date,
    )
    service = AnalysisService(database)
    service.seed_mock_business_data(shopee_date)
    service.seed_mock_business_data(tiktok_date)

    rows = AnalyticsRepository(database).load_analysis_rows(shopee_date)
    summary = service.run(shopee_date)

    assert all(row["legacy_commission_rate"] == "0.08" for row in rows)
    assert all(row["resolved_commission_rate"] is None for row in rows)
    assert all(row["fee_selection"] is None for row in rows)
    assert summary.profit_results == 0
    assert summary.pending_data == 1


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


def test_analysis_persists_complete_profit_input_lineage(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "lineage.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)

    summary = service.run(data_date)

    assert summary.profit_results == 4
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT input_name, status, source_type, source_date, age_days,
                   source_ref, detail_code, COUNT(*) AS row_count
            FROM profit_input_lineage WHERE data_date=?
            GROUP BY input_name, status, source_type, source_date, age_days,
                     source_ref, detail_code
            ORDER BY input_name
            """,
            (data_date.isoformat(),),
        ).fetchall()
    assert len(rows) == 9
    assert {row["input_name"] for row in rows} == {
        "gmv",
        "refunds",
        "commission_rate",
        "product_cost",
        "ad_spend",
        "seller_shipping",
        "coupons",
        "allocated_fixed_cost",
        "exchange_rate",
    }
    assert all(row["row_count"] == 4 for row in rows)
    assert all(row["status"] in {"ready", "carried_forward"} for row in rows)
    refund = next(row for row in rows if row["input_name"] == "refunds")
    assert tuple(refund)[1:7] == (
        "ready",
        "product_costs",
        data_date.isoformat(),
        0,
        "mock",
        "refund_observed",
    )


def test_legacy_zero_refund_without_source_stays_pending_lineage(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "pending-refund.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE product_costs
            SET refund_data_status='pending_data', input_source_name=''
            """
        )

    summary = service.run(data_date)

    assert summary.profit_results == 0
    assert summary.pending_data == 4
    with database.connect() as connection:
        refunds = connection.execute(
            """
            SELECT DISTINCT value_text, status, source_type, detail_code
            FROM profit_input_lineage
            WHERE data_date=? AND input_name='refunds'
            """,
            (data_date.isoformat(),),
        ).fetchall()
        messages = {
            row[0]
            for row in connection.execute(
                "SELECT message FROM alerts WHERE data_date=?",
                (data_date.isoformat(),),
            )
        }
    assert [tuple(row) for row in refunds] == [
        (None, "pending_data", "product_costs", "refund_source_missing")
    ]
    assert any("refund_amount" in message for message in messages)


def test_analysis_uses_t3_closed_zero_refund_fact(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "t3-refund.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_refund_inputs(
                platform, store, data_date, refund_amount_original,
                currency, status, source_type, source_ref,
                observed_on, closed_at, updated_at
            ) VALUES(
                'shopee', 'Shopee TH Store', ?, '0', 'THB',
                't3_closed_zero', 't3_no_refund_observed',
                't3_no_refund_observed', NULL, '2026-07-26',
                '2026-07-26T00:00:00Z'
            )
            """,
            (data_date.isoformat(),),
        )

    summary = service.run(data_date)

    assert summary.profit_results == 4
    with database.connect() as connection:
        lineage = connection.execute(
            """
            SELECT value_text, status, source_type, source_ref, detail_code
            FROM profit_input_lineage
            WHERE data_date=? AND input_name='refunds'
            LIMIT 1
            """,
            (data_date.isoformat(),),
        ).fetchone()
    assert tuple(lineage) == (
        "0",
        "ready",
        "daily_refund_inputs",
        "t3_no_refund_observed",
        "t3_no_refund_observed",
    )


def test_analysis_keeps_refund_pending_when_daily_fact_has_no_amount(
    tmp_path,
):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "daily-refund-pending.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_refund_inputs(
                platform, store, data_date, refund_amount_original,
                currency, status, source_type, source_ref,
                observed_on, closed_at, updated_at
            ) VALUES(
                'shopee', 'Shopee TH Store', ?, '0', 'THB',
                'pending_data', 'platform_refund_observations',
                'refund_amount_missing', NULL, NULL,
                '2026-07-26T00:00:00Z'
            )
            """,
            (data_date.isoformat(),),
        )

    summary = service.run(data_date)

    assert summary.profit_results == 0
    with database.connect() as connection:
        lineage = connection.execute(
            """
            SELECT value_text, status, source_type, source_ref, detail_code
            FROM profit_input_lineage
            WHERE data_date=? AND input_name='refunds'
            LIMIT 1
            """,
            (data_date.isoformat(),),
        ).fetchone()
    assert tuple(lineage) == (
        None,
        "pending_data",
        "daily_refund_inputs",
        "refund_amount_missing",
        "refund_source_missing",
    )


def test_daily_refund_fact_is_allocated_across_multiple_metric_rows(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "refund-allocation.sqlite3")
    database.migrate()

    class TwoMetricCollector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id=f"campaign-{index}",
                    sku_id=f"SKU-{index}",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(10),
                    attributed_gmv=Decimal(gmv),
                    orders=1,
                    source=self.source,
                )
                for index, gmv in enumerate((100, 300), start=1)
            ]

    PipelineRunner(database).run(TwoMetricCollector(), data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_refund_inputs(
                platform, store, data_date, refund_amount_original,
                currency, status, source_type, source_ref,
                observed_on, closed_at, updated_at
            ) VALUES(
                'shopee', 'shop', ?, '40', 'CNY', 'observed', 'test',
                'refund-1', '2026-07-24', NULL, '2026-07-24T00:00:00Z'
            )
            """,
            (data_date.isoformat(),),
        )

    rows = AnalyticsRepository(database).load_analysis_rows(data_date)

    assert [Decimal(row["refund_amount"]) for row in rows] == [
        Decimal(10),
        Decimal(30),
    ]


def test_pending_profit_inputs_remove_stale_strategy_recommendations(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "stale-recommendations.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    first = service.run(data_date)
    assert first.recommendations > 0
    with database.transaction() as connection:
        connection.execute(
            "UPDATE product_costs SET refund_data_status='pending_data'"
        )

    second = service.run(data_date)

    assert second.recommendations == 0
    with database.connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM recommendations WHERE data_date=?",
            (data_date.isoformat(),),
        ).fetchone()[0]
    assert remaining == 0


def test_verified_profit_does_not_require_strategy_or_inventory_inputs(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "financial-only.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute("DELETE FROM inventory_snapshots")
        connection.execute("DELETE FROM campaign_settings")

    summary = service.run(data_date)

    assert summary.profit_results == 4
    assert summary.recommendations == 0
    assert summary.pending_data == 4
    assert summary.capabilities["verified_profit"] == "ready"
    assert summary.capabilities["inventory_safe_strategy"] == "pending_data"
    with database.connect() as connection:
        codes = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT rule_code FROM alerts WHERE data_date=?",
                (data_date.isoformat(),),
            )
        }
    assert "missing_strategy_input" in codes


def test_fee_and_exchange_lineage_preserve_selected_source_dates(tmp_path):
    data_date = date(2026, 7, 29)
    database = Database(tmp_path / "source-dates.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    FeeStatementRepository(database).save(
        replace(
            _statement(),
            store="Shopee TH Store",
            source_store="store-alias",
        )
    )
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE exchange_rates
            SET source_date='2026-07-28', resolution_status='fallback_previous'
            WHERE rate_date=?
            """,
            (data_date.isoformat(),),
        )

    service.run(data_date)

    with database.connect() as connection:
        rows = {
            row["input_name"]: row
            for row in connection.execute(
                """
                SELECT DISTINCT input_name, status, source_type, source_date,
                                age_days, source_ref, detail_code
                FROM profit_input_lineage
                WHERE data_date=? AND input_name IN (
                    'commission_rate', 'exchange_rate'
                )
                """,
                (data_date.isoformat(),),
            )
        }
    assert tuple(rows["commission_rate"])[1:] == (
        "carried_forward",
        "platform_fee_statements",
        "2026-07-26",
        3,
        "weekly_report.pdf",
        "carried_forward",
    )
    assert tuple(rows["exchange_rate"])[1:] == (
        "carried_forward",
        "exchange_rates",
        "2026-07-28",
        1,
        "fallback_previous",
        "fallback_previous",
    )


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
            PipelineRunner(database).run(FailingCollector(), date(2026, 7, 23))
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
                    spend=Decimal(100),
                    attributed_gmv=Decimal(100),
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
                    spend=Decimal(100),
                    attributed_gmv=Decimal(80),
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
            ) VALUES (
                'shopee', 'shopee-campaign-1', 'SHOPEE-SKU-001', '300', 1
            )
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


def test_single_metric_uses_mapped_order_cost_cny(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="虾皮泰国",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=day,
                    currency="THB",
                    spend=Decimal(100),
                    attributed_gmv=Decimal(1000),
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_cost_lines(
                platform, store, order_id, sku_id, order_date, quantity,
                unit_cost_cny, line_cost_cny, source_file
            ) VALUES (
                'shopee', 'no4kud44da', 'order', '1 bag', '2026-07-23',
                1, '75', '75', 'orders.xlsx'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO store_aliases(
                platform, source_store, canonical_store
            ) VALUES ('shopee', 'no4kud44da', '虾皮泰国')
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]
    assert Decimal(row["order_product_cost_cny"]) == Decimal(75)
    assert row["order_cost_allocation_ambiguous"] == 0

    summary = service.run(data_date)

    assert summary.profit_results == 1
    with database.connect() as connection:
        result = connection.execute(
            """
            SELECT gross_profit_cny, net_profit_cny
            FROM profit_results WHERE data_date='2026-07-23'
            """
        ).fetchone()
    assert tuple(result) == ("118.20", "96.36")


def test_multiple_metrics_flag_ambiguous_order_cost_allocation(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="虾皮泰国",
                    account_id="account",
                    campaign_id=f"campaign-{index}",
                    sku_id=f"SKU-{index}",
                    data_date=day,
                    currency="THB",
                    spend=Decimal(100),
                    attributed_gmv=Decimal(300),
                    orders=1,
                    source=self.source,
                )
                for index in range(2)
            ]

    PipelineRunner(database).run(Collector(), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_cost_lines(
                platform, store, order_id, sku_id, order_date, quantity,
                unit_cost_cny, line_cost_cny, source_file
            ) VALUES (
                'shopee', 'no4kud44da', 'order', '1 bag', '2026-07-23',
                1, '75', '75', 'orders.xlsx'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO store_aliases(
                platform, source_store, canonical_store
            ) VALUES ('shopee', 'no4kud44da', '虾皮泰国')
            """
        )

    rows = AnalyticsRepository(database).load_analysis_rows(data_date)
    assert all(row["order_product_cost_cny"] is None for row in rows)
    assert all(row["order_cost_allocation_ambiguous"] == 1 for row in rows)

    service.run(data_date)

    with database.connect() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM alerts
            WHERE rule_code='ambiguous_order_cost_allocation'
              AND data_date='2026-07-23'
            """
        ).fetchone()[0]
    assert count == 2


def test_analysis_reads_confirmed_inventory_cost_snapshot(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="SKU-1",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    AnalysisService(database).seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_cost_snapshots(
                platform, store, order_id, seller_sku, quantity,
                unit_cost_cny, total_cost_cny, cost_effective_date,
                status, created_at
            ) VALUES (
                'shopee','shop','ORDER-1','SKU-1',2,
                '5','10','2026-07-01','confirmed',
                '2026-07-24T00:00:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO inventory_movements(
                id, seller_sku, movement_type, quantity_delta,
                occurred_on, source_type, source_id, note, created_at
            ) VALUES (
                'move-1','SKU-1','sale_out',-2,'2026-07-23',
                'order','shopee:shop:ORDER-1','',
                '2026-07-24T00:00:00Z'
            )
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]

    assert Decimal(row["order_product_cost_cny"]) == Decimal(10)


def test_analysis_ignores_legacy_placeholder_when_resolved_order_line_exists(
    tmp_path,
):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "duplicate-order-analysis.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="SKU-1",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    with database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO platform_order_lines VALUES(
                'shopee', 'shop', 'ORDER-1', ?, ?, 'SKU-1', '',
                'Product', 1, '100', 'CNY', 'completed', 'delivered', '',
                '2026-07-23', ?
            )
            """,
            (
                ("SKU-1", "SKU-1", "2026-07-23T00:00:00Z"),
                ("item-1", "model-1", "2026-07-24T00:00:00Z"),
            ),
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
                'shopee', 'shop', 'ORDER-1', 'SKU-1',
                'supplier_fulfilled', '2026-07-01', 'available',
                'sku_policy', '2026-07-24T00:00:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
                'shopee', 'shop', 'ORDER-1', 'SKU-1', 1, '10', '10',
                '2026-07-01', 'confirmed', '2026-07-24T00:00:00Z'
            )
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]

    assert row["order_cost_coverage_incomplete"] == 0
    assert Decimal(row["order_product_cost_cny"]) == Decimal(10)


def test_partial_order_cost_snapshots_keep_product_cost_pending(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "partial-order-cost.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        for index in (1, 2):
            connection.execute(
                """
                INSERT INTO platform_order_lines VALUES(
                  'shopee','shop',?, ?, ?, ?, 'variant','Product',1,
                  '50','CNY','completed','delivered','',
                  '2026-07-23','2026-07-23T00:00:00Z')
                """,
                (
                    f"ORDER-{index}",
                    f"item-{index}",
                    f"model-{index}",
                    f"SKU-{index}",
                ),
            )
            connection.execute(
                """
                INSERT INTO order_fulfillment_snapshots VALUES(
                  'shopee','shop',?,?,'supplier_fulfilled','2026-07-01',
                  'available','sku_policy','2026-07-23T00:00:00Z')
                """,
                (f"ORDER-{index}", f"SKU-{index}"),
            )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','ORDER-1','SKU-1',2,'10','20',
              '2026-07-01','confirmed','2026-07-23T00:00:00Z')
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]
    summary = service.run(data_date)

    assert row["order_product_cost_cny"] is None
    assert row["order_cost_coverage_incomplete"] == 1
    assert summary.profit_results == 0
    with database.connect() as connection:
        lineage = connection.execute(
            """
            SELECT status, source_type, detail_code
            FROM profit_input_lineage
            WHERE data_date=? AND input_name='product_cost'
            """,
            (data_date.isoformat(),),
        ).fetchone()
    assert tuple(lineage) == (
        "pending_data",
        "order_cost_snapshots",
        "order_cost_coverage_incomplete",
    )


def test_returned_order_line_is_excluded_from_cost_coverage(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "returned-cost-coverage.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=2,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','RETURNED','item-r','model-r','SKU-R',
              'variant','Product',1,'50','CNY','completed','delivered',
              'returned','2026-07-23','2026-07-24T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','RETAINED','item-k','model-k','SKU-K',
              'variant','Product',1,'50','CNY','completed','delivered','',
              '2026-07-23','2026-07-24T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
              'shopee','shop','RETAINED','SKU-K','supplier_fulfilled',
              '2026-07-01','available','sku_policy',
              '2026-07-24T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','RETAINED','SKU-K',1,'10','10',
              '2026-07-01','confirmed','2026-07-24T00:00:00Z')
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]

    assert row["order_cost_coverage_incomplete"] == 0
    assert Decimal(row["order_product_cost_cny"]) == Decimal(10)


def test_frozen_order_cost_snapshot_takes_priority_over_legacy_cost(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "snapshot-priority.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    AnalysisService(database).seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','ORDER-1','item','model','SKU-1','variant',
              'Product',1,'100','CNY','completed','delivered','',
              '2026-07-23','2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
              'shopee','shop','ORDER-1','SKU-1','supplier_fulfilled',
              '2026-07-01','available','sku_policy','2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','ORDER-1','SKU-1',1,'10','10',
              '2026-07-01','confirmed','2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_lines VALUES(
              'shopee','shop','ORDER-1','SKU-1','2026-07-23',1,
              '75','75','legacy.xlsx','2026-07-23T00:00:00Z')
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]

    assert Decimal(row["order_product_cost_cny"]) == Decimal(10)
    assert row["cost_source_type"] == "order_cost_snapshots"


def test_legacy_order_cost_requires_matching_order_and_sku_keys(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "legacy-key-coverage.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=2,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        for index in (1, 2):
            connection.execute(
                """
                INSERT INTO platform_order_lines VALUES(
                  'shopee','shop',?, ?, ?, ?, 'variant','Product',1,
                  '50','CNY','completed','delivered','',
                  '2026-07-23','2026-07-23T00:00:00Z')
                """,
                (
                    f"ORDER-{index}",
                    f"item-{index}",
                    f"model-{index}",
                    f"SKU-{index}",
                ),
            )
            connection.execute(
                """
                INSERT INTO order_cost_lines VALUES(
                  'shopee','shop',?,?,'2026-07-23',1,'85','85',
                  'wrong.xlsx','2026-07-23T00:00:00Z')
                """,
                (f"WRONG-{index}", f"OTHER-{index}"),
            )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]
    summary = service.run(data_date)

    assert row["order_product_cost_cny"] is None
    assert row["order_cost_coverage_incomplete"] == 1
    assert summary.profit_results == 0


def test_legacy_order_cost_rejects_extra_unmatched_cost_rows(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "legacy-extra-key.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=2,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    service = AnalysisService(database)
    service.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        for index in (1, 2):
            connection.execute(
                """
                INSERT INTO platform_order_lines VALUES(
                  'shopee','shop',?, ?, ?, ?, 'variant','Product',1,
                  '50','CNY','completed','delivered','',
                  '2026-07-23','2026-07-23T00:00:00Z')
                """,
                (
                    f"ORDER-{index}",
                    f"item-{index}",
                    f"model-{index}",
                    f"SKU-{index}",
                ),
            )
            connection.execute(
                """
                INSERT INTO order_cost_lines VALUES(
                  'shopee','shop',?,?,'2026-07-23',1,'10','10',
                  'legacy.xlsx','2026-07-23T00:00:00Z')
                """,
                (f"ORDER-{index}", f"SKU-{index}"),
            )
        connection.execute(
            """
            INSERT INTO order_cost_lines VALUES(
              'shopee','shop','WRONG','OTHER','2026-07-23',1,'99','99',
              'legacy.xlsx','2026-07-23T00:00:00Z')
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]
    summary = service.run(data_date)

    assert row["order_product_cost_cny"] is None
    assert row["order_cost_coverage_incomplete"] == 1
    assert summary.profit_results == 0


def test_supplier_fulfilled_cost_does_not_require_inventory_inputs(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="shop",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="SKU-1",
                    data_date=day,
                    currency="CNY",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    AnalysisService(database).seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute("DELETE FROM inventory_snapshots")
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','ORDER-1','item','model','SKU-1',
              '1 bag','Product',2,'100','CNY','completed','delivered','',
              '2026-07-23','2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
              'shopee','shop','ORDER-1','SKU-1','supplier_fulfilled',
              '2026-07-01','available','sku_policy',
              '2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','ORDER-1','SKU-1',2,'5','10',
              '2026-07-01','confirmed','2026-07-23T00:00:00Z')
            """
        )

    row = AnalyticsRepository(database).load_analysis_rows(data_date)[0]
    summary = AnalysisService(database).run(data_date)

    assert row["inventory_required"] == 0
    assert Decimal(row["order_product_cost_cny"]) == Decimal(10)
    assert summary.profit_results == 1
    assert summary.capabilities["inventory_safe_strategy"] == "not_applicable"

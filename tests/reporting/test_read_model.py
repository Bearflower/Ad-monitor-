from dataclasses import replace
from datetime import date
from decimal import Decimal

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import DailyAdMetric, Platform
from adwatch.income.repository import FeeStatementRepository
from adwatch.pipeline.runner import PipelineRunner
from adwatch.reporting.read_model import ReportReadModel
from adwatch.storage.db import Database
from tests.income.test_repository import _statement


def _insert_complete_lineage(
    connection,
    *,
    platform: str,
    store: str,
    account_id: str,
    campaign_id: str,
    sku_id: str,
    data_date: str,
) -> None:
    connection.executemany(
        """
        INSERT INTO profit_input_lineage(
            platform, store, account_id, campaign_id, sku_id, data_date,
            input_name, value_text, status, source_type, source_date,
            age_days, source_ref, detail_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '0', 'ready', 'test', ?, 0, '', '')
        """,
        (
            (
                platform,
                store,
                account_id,
                campaign_id,
                sku_id,
                data_date,
                input_name,
                data_date,
            )
            for input_name in (
                "gmv",
                "refunds",
                "commission_rate",
                "product_cost",
                "ad_spend",
                "seller_shipping",
                "coupons",
                "allocated_fixed_cost",
                "exchange_rate",
            )
        ),
    )


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


def test_dashboard_trend_includes_net_profit_when_analysis_is_complete(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
    analysis.run(data_date)

    point = ReportReadModel(database).dashboard(data_date).trends[7][-1]

    assert point.data_date == data_date
    assert point.net_profit is not None


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


def test_daily_read_model_exposes_distinct_fee_rate_lineage(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "fee-read-model.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
    FeeStatementRepository(database).save(
        replace(
            _statement(),
            store="Shopee TH Store",
            source_store="store-alias",
        )
    )
    analysis.run(data_date)

    fee_rates = ReportReadModel(database).daily(data_date).fee_rates

    assert len(fee_rates) == 1
    assert fee_rates[0].platform == "shopee"
    assert fee_rates[0].store == "Shopee TH Store"
    assert fee_rates[0].effective_rate == Decimal("0.237201")
    assert fee_rates[0].period_start == date(2026, 7, 20)
    assert fee_rates[0].period_end == date(2026, 7, 26)
    assert fee_rates[0].selection == "covering_period"
    assert fee_rates[0].source_name == "weekly_report.pdf"
    assert fee_rates[0].source_sha256 == "a" * 64


def test_daily_read_model_keeps_store_with_missing_fee_rate_visible(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "missing-store-fee.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
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
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, source
            ) VALUES (
                'shopee', 'Shopee Other Store', 'other-account',
                'other-campaign', 'OTHER-SKU', ?, 'THB', '10', '20', 1,
                'test'
            )
            """,
            (data_date.isoformat(),),
        )
    analysis.run(data_date)

    fee_rates = ReportReadModel(database).daily(data_date).fee_rates

    assert [(item.store, item.selection) for item in fee_rates] == [
        ("Shopee Other Store", "pending_data"),
        ("Shopee TH Store", "covering_period"),
    ]
    pending = fee_rates[0]
    assert pending.effective_rate is None
    assert pending.source_name is None


def test_daily_read_model_exposes_store_source_coverage_and_lineage(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "source-coverage.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        for order_id, amount in (("ORDER-1", "20"), ("ORDER-2", None)):
            connection.execute(
                """
                INSERT INTO platform_order_lines(
                    platform, store, order_id, item_id, model_id, seller_sku,
                    variation_name, product_name, quantity, buyer_paid,
                    currency, order_status, logistics_status, refund_status,
                    ordered_at, source_updated_at
                ) VALUES (
                    'shopee', 'Shopee TH Store', ?, ?, ?, ?, 'variant',
                    'product', 1, '100', 'THB', 'completed', 'delivered',
                    'observed', ?, '2026-07-24T00:00:00+00:00'
                )
                """,
                (
                    order_id,
                    f"ITEM-{order_id}",
                    f"MODEL-{order_id}",
                    f"SKU-{order_id}",
                    data_date.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO platform_refund_observations(
                    platform, store, order_id, observed_on, order_date,
                    refund_status, amount_original, currency,
                    source_updated_at, observed_at, source
                ) VALUES (
                    'shopee', 'Shopee TH Store', ?, '2026-07-24', ?,
                    'observed', ?, 'THB', '2026-07-24T00:00:00+00:00',
                    '2026-07-24T00:00:00+00:00', 'test'
                )
                """,
                (order_id, data_date.isoformat(), amount),
            )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots(
                platform, store, order_id, seller_sku, quantity,
                unit_cost_cny, total_cost_cny, cost_effective_date,
                status, created_at
            ) VALUES (
                'shopee', 'Shopee TH Store', 'ORDER-1', 'SKU-ORDER-1', 1,
                '10', '10', ?, 'confirmed', '2026-07-24'
            )
            """,
            (data_date.isoformat(),),
        )
        for index in (1, 2):
            connection.execute(
                """
                INSERT INTO platform_inventory_snapshots(
                    platform, store, item_id, model_id, seller_sku,
                    snapshot_date, available_units, observed_at, source
                ) VALUES (
                    'shopee', 'Shopee TH Store', ?, ?, ?,
                    ?, 20, '2026-07-23T01:00:00+00:00', 'test'
                )
                """,
                (
                    f"ITEM-{index}",
                    f"MODEL-{index}",
                    f"SKU-ORDER-{index}",
                    data_date.isoformat(),
                ),
            )
    analysis.run(data_date)

    snapshot = ReportReadModel(database).daily(data_date)
    source = snapshot.profit_sources[0]

    assert source.platform == "shopee"
    assert source.store == "Shopee TH Store"
    assert source.metric_rows == 4
    assert source.platform_orders == 2
    assert source.confirmed_cost_orders == 1
    assert source.refund_observations == 2
    assert source.refund_amount_observations == 1
    assert source.inventory_snapshot_date == data_date
    assert source.exchange_source_date == data_date
    assert source.profit_status == "pending_data"
    assert source.missing_inputs == ("product_cost",)
    lineage = [
        item
        for item in snapshot.profit_input_lineage
        if item.platform == "shopee" and item.store == "Shopee TH Store"
    ]
    assert {item.input_name for item in lineage} == {
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
    assert all(item.covered_rows == 4 for item in lineage)
    assert all(item.total_rows == 4 for item in lineage)
    assert snapshot.capabilities["verified_profit"] == "pending_data"
    assert snapshot.capabilities["inventory_safe_strategy"] == "pending_data"


def test_source_coverage_excludes_ambiguous_resolved_order_lines(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "ambiguous-order-source.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, source
            ) VALUES (
                'shopee', 'Shopee TH Store', 'account', 'campaign',
                '__ALL__', ?, 'THB', '10', '20', 1, 'test'
            )
            """,
            (data_date.isoformat(),),
        )
        for item_id, model_id, updated_at in (
            ("ITEM-1", "MODEL-1", "2026-07-23T00:00:00+00:00"),
            ("ITEM-2", "MODEL-2", "2026-07-24T00:00:00+00:00"),
        ):
            connection.execute(
                """
                INSERT INTO platform_order_lines VALUES(
                  'shopee','Shopee TH Store','ORDER-1',?,?, 'SKU-1',
                  'variant','Product',1,'100','THB','completed','delivered',
                  '',?,?)
                """,
                (item_id, model_id, data_date.isoformat(), updated_at),
            )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','Shopee TH Store','ORDER-1','SKU-1',1,'10','10',
              '2026-07-01','confirmed','2026-07-24T00:00:00+00:00')
            """
        )

    with database.connect() as connection:
        sources, _, _ = ReportReadModel._source_views(
            connection, data_date.isoformat(), ()
        )

    assert sources[0].platform_orders == 0
    assert sources[0].confirmed_cost_orders == 0


def test_inventory_strategy_gate_rejects_unrelated_or_stale_snapshots(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "inventory-gate.sqlite3")
    database.migrate()

    class Collector:
        source = "test"
        platform = Platform.SHOPEE

        def collect(self, day):
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="Shopee TH Store",
                    account_id="account",
                    campaign_id="campaign",
                    sku_id="__ALL__",
                    data_date=day,
                    currency="THB",
                    spend=Decimal(20),
                    attributed_gmv=Decimal(100),
                    orders=1,
                    source=self.source,
                )
            ]

    PipelineRunner(database).run(Collector(), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','Shopee TH Store','ORDER-1','ITEM-1','MODEL-1',
              'ORDER-SKU','variant','Product',1,'100','THB','completed',
              'delivered','','2026-07-23','2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO platform_inventory_snapshots VALUES(
              'shopee','Shopee TH Store','OLD-ITEM','OLD-MODEL','ORDER-SKU',
              '2026-07-22',10,'2026-07-22T00:00:00Z','test')
            """
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
              'shopee','Shopee TH Store','ORDER-1','ORDER-SKU',
              'supplier_fulfilled','2026-07-01','available','sku_policy',
              '2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','Shopee TH Store','ORDER-1','ORDER-SKU',1,'10','10',
              '2026-07-01','confirmed','2026-07-23T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO platform_inventory_snapshots VALUES(
              'shopee','Shopee TH Store','OTHER-ITEM','OTHER-MODEL','OTHER-SKU',
              '2026-07-23',10,'2026-07-23T00:00:00Z','test')
            """
        )
    analysis.run(data_date)

    snapshot = ReportReadModel(database).daily(data_date)

    assert snapshot.profit_sources[0].inventory_snapshot_date == data_date
    assert snapshot.capabilities["verified_profit"] == "ready"
    assert snapshot.capabilities["inventory_safe_strategy"] == "pending_data"


def test_daily_capabilities_are_partial_when_only_one_store_has_lineage(
    tmp_path,
):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "partial-lineage.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, source
            ) VALUES (
                'shopee', 'Shopee Missing Store', 'missing-account',
                'missing-campaign', 'MISSING-SKU', ?, 'THB', '10', '20', 1,
                'test'
            )
            """,
            (data_date.isoformat(),),
        )
    analysis.run(data_date)

    snapshot = ReportReadModel(database).daily(data_date)

    assert snapshot.capabilities["estimated_profit"] == "partial"
    assert snapshot.capabilities["verified_profit"] == "partial"
    missing = next(
        item
        for item in snapshot.profit_sources
        if item.store == "Shopee Missing Store"
    )
    assert missing.profit_status == "pending_data"
    assert "refunds" in missing.missing_inputs
    assert "product_cost" in missing.missing_inputs


def test_daily_read_model_includes_reconciliation_aware_break_even_target(
    tmp_path,
):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, roas, cpa, source
            ) VALUES (
                'shopee', '虾皮泰国', 'account', 'Shop GMV Max', '__ALL__',
                '2026-07-28', 'THB', '210.10', '179.00', 2,
                '0.8520', '105.0500', 'ziniao-cli'
            )
            """
        )
        _insert_complete_lineage(
            connection,
            platform="shopee",
            store="虾皮泰国",
            account_id="account",
            campaign_id="Shop GMV Max",
            sku_id="__ALL__",
            data_date="2026-07-28",
        )
        connection.execute(
            """
            INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
            VALUES ('THB', '2026-07-28', '0.201468432624856')
            """
        )
        connection.execute(
            """
            INSERT INTO profit_results(
                platform, store, account_id, campaign_id, sku_id, data_date,
                net_sales_cny, platform_commission_cny, gross_profit_cny,
                net_profit_cny, break_even_roas
            ) VALUES (
                'shopee', '虾皮泰国', 'account', 'Shop GMV Max', '__ALL__',
                '2026-07-28', '36.06', '8.55', '17.70', '-24.63', '1.71'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO platform_order_lines(
                platform, store, order_id, item_id, model_id, seller_sku,
                variation_name, product_name, quantity, buyer_paid, currency,
                order_status, logistics_status, refund_status, ordered_at,
                source_updated_at
            ) VALUES (
                'shopee', 'no4kud44da', 'ORDER-1', 'ITEM-1', 'MODEL-1',
                'SKU-1', '1 bag', 'Product', 1, '179', 'THB', 'completed',
                'delivered', 'none', '2026-07-28', '2026-07-29'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots(
                platform, store, order_id, seller_sku, quantity,
                unit_cost_cny, total_cost_cny, cost_effective_date,
                status, created_at
            ) VALUES (
                'shopee', 'no4kud44da', 'ORDER-1', 'SKU-1', 1,
                '9.81', '9.81', '2026-07-28', 'confirmed', '2026-07-29'
            )
            """
        )

    platform = ReportReadModel(database).daily(
        date(2026, 7, 28)
    ).platforms[0]
    target = platform.break_even_target

    assert target is not None
    assert target.break_even_roas == Decimal("2.04")
    assert target.break_even_gmv == Decimal("428.03")
    assert target.break_even_orders == 5
    assert target.confidence == "reconciliation_pending"
    assert target.explanation == "广告归因 2 单，当前匹配 1 个实际成本订单"


def test_legacy_profit_and_recommendation_without_lineage_stay_hidden(tmp_path):
    data_date = date(2026, 7, 28)
    database = Database(tmp_path / "legacy-profit.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, source
            ) VALUES (
                'shopee','shop','account','campaign','SKU-1',?,
                'CNY','10','30',1,'legacy')
            """,
            (data_date.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO profit_results(
                platform, store, account_id, campaign_id, sku_id, data_date,
                net_sales_cny, platform_commission_cny, gross_profit_cny,
                net_profit_cny, break_even_roas
            ) VALUES (
                'shopee','shop','account','campaign','SKU-1',?,
                '30','3','20','10','1.5')
            """,
            (data_date.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO recommendations(
                rule_code, platform, campaign_id, sku_id, data_date,
                action, reason, requires_approval, store_id
            ) VALUES (
                'legacy-rule','shopee','campaign','SKU-1',?,
                'pause','legacy',1,'shop')
            """,
            (data_date.isoformat(),),
        )

    snapshot = ReportReadModel(database).daily(data_date)
    trend = ReportReadModel(database).dashboard(data_date).trends[7][-1]

    assert snapshot.platforms[0].net_profit is None
    assert snapshot.sku_performance[0].net_profit is None
    assert snapshot.capabilities["verified_profit"] == "pending_data"
    assert snapshot.recommendations == ()
    assert trend.net_profit is None


def test_read_model_finds_latest_date_and_detects_real_source(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        for data_date, source in (
            ("2026-07-27", "mock"),
            ("2026-07-28", "ziniao-cli"),
        ):
            connection.execute(
                """
                INSERT INTO daily_ad_metrics(
                    platform, store, account_id, campaign_id, sku_id,
                    data_date, currency, spend, attributed_gmv, orders,
                    source
                ) VALUES (
                    'shopee', 'shop', 'account', 'campaign', '__ALL__',
                    ?, 'THB', '10', '20', 1, ?
                )
                """,
                (data_date, source),
            )

    read_model = ReportReadModel(database)

    assert read_model.latest_data_date() == date(2026, 7, 28)
    assert read_model.is_simulated(date(2026, 7, 27)) is True
    assert read_model.is_simulated(date(2026, 7, 28)) is False

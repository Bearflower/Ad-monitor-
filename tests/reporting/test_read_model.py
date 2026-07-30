from datetime import date
from decimal import Decimal

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


def test_daily_read_model_marks_supplier_fulfilled_inventory_not_applicable(
    tmp_path,
):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, source
            ) VALUES(
                'shopee','shop','account','campaign','__ALL__',
                '2026-07-29','THB','10','100',1,'ziniao-cli'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO profit_results(
                platform, store, account_id, campaign_id, sku_id, data_date,
                net_sales_cny, platform_commission_cny, gross_profit_cny,
                net_profit_cny, break_even_roas
            ) VALUES(
                'shopee','shop','account','campaign','__ALL__',
                '2026-07-29','20','4','10','8','2'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
                'shopee','shop','ORDER-1','item','model','SKU-1',
                '1 bag','Product',1,'100','THB','pending','pending','',
                '2026-07-29','2026-07-30T00:00:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
                'shopee','shop','ORDER-1','SKU-1','supplier_fulfilled',
                '2026-07-01','available','sku_policy',
                '2026-07-30T00:00:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
                'shopee','shop','ORDER-1','SKU-1',1,'5','5',
                '2026-07-01','confirmed','2026-07-30T00:00:00Z'
            )
            """
        )

    snapshot = ReportReadModel(database).daily(date(2026, 7, 29))

    assert (
        snapshot.capabilities["inventory_safe_strategy"]
        == "not_applicable"
    )

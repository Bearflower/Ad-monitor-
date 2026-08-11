from datetime import date
from decimal import Decimal

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.reporting.break_even import BreakEvenTarget
from adwatch.reporting.markdown import (
    present_daily_report,
    render_daily_markdown,
    render_monthly_markdown,
)
from adwatch.reporting.read_model import (
    DailySnapshot,
    FeeRateSummary,
    PlatformSummary,
    ProfitInputLineageView,
    ProfitSourceSummary,
    ReportReadModel,
)
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

    for heading in (
        "一、核心经营结果",
        "二、保本目标",
        "三、平台表现",
        "四、异常与风险",
        "五、建议动作",
    ):
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
    assert "数据可信度" in report
    assert "估算利润：待补数据" in report


def test_monthly_report_aggregates_available_daily_snapshots(tmp_path):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)

    report = render_monthly_markdown(database, 2026, 7)

    assert "# 广告月报｜2026年07月" in report
    assert "Shopee" in report


def test_daily_report_is_chinese_and_explains_risk_and_action():
    snapshot = DailySnapshot(
        data_date=date(2026, 7, 27),
        platforms=(
            PlatformSummary(
                platform="shopee",
                spend=Decimal(400),
                gmv=Decimal(1066),
                orders=5,
                roas=Decimal("2.665"),
                net_profit=Decimal("-35.20"),
                attributed_sales_cny=Decimal("214.79"),
                platform_fee_cny=Decimal("50.95"),
                ad_spend_cny=Decimal("80.95"),
                sku_and_other_cost_cny=Decimal("118.09"),
            ),
        ),
        sku_performance=(),
        alerts=(
            {
                "rule_code": "spend_jump",
                "severity": "warning",
                "message": "Spend increased by more than 30%",
            },
        ),
        recommendations=(),
        capabilities={
            "platform_metrics": "ready",
            "estimated_profit": "ready",
            "verified_profit": "ready",
            "inventory_safe_strategy": "pending_data",
        },
    )

    presentation = present_daily_report(snapshot, simulated=False)

    assert presentation.risk_label == "高风险"
    assert presentation.header_template == "red"
    assert "广告归因销售额：¥214.79" in presentation.markdown
    assert "SKU 成本及其他费用：¥118.09" in presentation.markdown
    assert "平台综合费用：¥50.95" in presentation.markdown
    assert "广告费用：¥80.95" in presentation.markdown
    assert "净利润：-¥35.20" in presentation.markdown
    assert "广告花费较基线增长超过 30%" in presentation.markdown
    assert "建议：暂不调整" in presentation.markdown
    assert "原因：" in presentation.markdown
    assert "平台广告数据：已就绪" in presentation.markdown
    assert "库存安全策略：待补数据" in presentation.markdown
    assert "pending_data" not in presentation.markdown
    assert "TOP3/BOTTOM3" not in presentation.markdown


def test_daily_report_displays_fee_rate_period_and_safe_source_name():
    snapshot = DailySnapshot(
        data_date=date(2026, 7, 27),
        platforms=(),
        sku_performance=(),
        alerts=(),
        recommendations=(),
        capabilities={},
        fee_rates=(
            FeeRateSummary(
                platform="shopee",
                store="虾皮泰国",
                effective_rate=Decimal("0.237201"),
                period_start=date(2026, 7, 20),
                period_end=date(2026, 7, 26),
                selection="carried_forward",
                source_name="weekly_report_20260720.pdf",
                source_sha256="a" * 64,
            ),
        ),
    )

    report = render_daily_markdown(snapshot, simulated=False)

    assert "## 七、平台费率来源" in report
    assert "Shopee / 虾皮泰国：23.7201%" in report
    assert "2026-07-20 至 2026-07-26" in report
    assert "沿用最近周期" in report
    assert "weekly_report_20260720.pdf" in report
    assert "aaaaaaaa" not in report


def test_daily_report_marks_fee_rate_as_pending_without_fake_zero():
    snapshot = DailySnapshot(
        data_date=date(2026, 7, 27),
        platforms=(),
        sku_performance=(),
        alerts=(),
        recommendations=(),
        capabilities={},
    )

    report = render_daily_markdown(snapshot, simulated=False)

    assert "平台费率：待补数据" in report
    assert "0.0000%" not in report


def test_daily_report_lists_store_source_freshness_and_missing_inputs():
    snapshot = DailySnapshot(
        data_date=date(2026, 7, 27),
        platforms=(),
        sku_performance=(),
        alerts=(),
        recommendations=(),
        capabilities={
            "platform_metrics": "ready",
            "estimated_profit": "partial",
            "verified_profit": "pending_data",
            "inventory_safe_strategy": "pending_data",
        },
        profit_sources=(
            ProfitSourceSummary(
                platform="shopee",
                store="虾皮泰国",
                metric_rows=1,
                attributed_orders=2,
                platform_orders=3,
                confirmed_cost_orders=2,
                refund_observations=3,
                refund_amount_observations=1,
                inventory_snapshot_date=date(2026, 7, 26),
                exchange_source_date=date(2026, 7, 26),
                exchange_resolution_status="fallback_previous",
                fee_period_start=date(2026, 7, 20),
                fee_period_end=date(2026, 7, 26),
                fee_selection="carried_forward",
                profit_status="pending_data",
                missing_inputs=("refunds", "product_cost"),
            ),
        ),
        profit_input_lineage=(
            ProfitInputLineageView(
                platform="shopee",
                store="虾皮泰国",
                input_name="exchange_rate",
                status="carried_forward",
                source_type="exchange_rates",
                source_date=date(2026, 7, 26),
                age_days=1,
                source_ref="fallback_previous",
                detail_code="fallback_previous",
                covered_rows=1,
                total_rows=1,
            ),
        ),
    )

    report = render_daily_markdown(snapshot, simulated=False)

    assert "## 八、数据来源与新鲜度" in report
    assert "Shopee / 虾皮泰国：利润待补数据" in report
    assert "广告指标 1 行 / 归因订单 2 单 / 平台订单 3 单" in report
    assert "成本覆盖：2/3 个平台订单" in report
    assert "退款金额观测：1/3 个退款观测" in report
    assert "库存快照：2026-07-26" in report
    assert "汇率来源：2026-07-26（fallback_previous）" in report
    assert "结算费率周期：2026-07-20 至 2026-07-26（沿用最近周期）" in report
    assert "关键缺口：退款金额、商品成本" in report
    assert "汇率：沿用已有数据｜exchange_rates｜2026-07-26｜1/1 行" in report
    assert "估算利润：部分就绪" in report
    assert "ORDER-" not in report
    assert "/Users/" not in report


def test_daily_report_marks_one_store_pending_beside_ready_store():
    snapshot = DailySnapshot(
        data_date=date(2026, 7, 27),
        platforms=(),
        sku_performance=(),
        alerts=(),
        recommendations=(),
        capabilities={},
        fee_rates=(
            FeeRateSummary(
                platform="shopee",
                store="Store A",
                effective_rate=Decimal("0.2"),
                period_start=date(2026, 7, 20),
                period_end=date(2026, 7, 26),
                selection="carried_forward",
                source_name="weekly_report.pdf",
                source_sha256="a" * 64,
            ),
            FeeRateSummary(
                platform="shopee",
                store="Store B",
                effective_rate=None,
                period_start=None,
                period_end=None,
                selection="pending_data",
                source_name=None,
                source_sha256=None,
            ),
        ),
    )

    report = render_daily_markdown(snapshot, simulated=False)

    assert "Shopee / Store A：20.0000%" in report
    assert "Shopee / Store B：待补数据" in report


def test_daily_report_displays_break_even_target_and_reconciliation_status():
    snapshot = DailySnapshot(
        data_date=date(2026, 7, 28),
        platforms=(
            PlatformSummary(
                platform="shopee",
                spend=Decimal("210.10"),
                gmv=Decimal("179.00"),
                orders=2,
                roas=Decimal("0.8520"),
                net_profit=Decimal("-24.63"),
                attributed_sales_cny=Decimal("36.06"),
                platform_fee_cny=Decimal("8.55"),
                ad_spend_cny=Decimal("42.33"),
                sku_and_other_cost_cny=Decimal("9.81"),
                break_even_target=BreakEvenTarget(
                    break_even_roas=Decimal("2.04"),
                    break_even_gmv=Decimal("428.03"),
                    average_order_value=Decimal("89.50"),
                    break_even_orders=5,
                    gmv_gap=Decimal("249.03"),
                    order_gap=3,
                    confidence="reconciliation_pending",
                    explanation="广告归因 2 单，当前匹配 1 个实际成本订单",
                ),
            ),
        ),
        sku_performance=(),
        alerts=(),
        recommendations=(),
        capabilities={
            "platform_metrics": "ready",
            "estimated_profit": "ready",
            "verified_profit": "ready",
            "inventory_safe_strategy": "pending_data",
        },
    )

    report = render_daily_markdown(snapshot, simulated=False)

    assert "## 二、保本目标" in report
    assert "Shopee 当前 ROAS：0.85" in report
    assert "Shopee 经营保本 ROAS：2.04" in report
    assert "当前花费下保本 GMV：THB 428.03" in report
    assert "当前客单价：THB 89.50" in report
    assert "估算保本单量：约 5 单" in report
    assert "距离保本：还差 THB 249.03 / 约 3 单" in report
    assert "数据可信度：待对账" in report
    assert "广告归因 2 单，当前匹配 1 个实际成本订单" in report
    assert "## 三、平台表现" in report
    assert "## 六、数据可信度" in report


def test_daily_report_marks_unavailable_break_even_without_fake_numbers(
    tmp_path,
):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)

    report = render_daily_markdown(
        ReportReadModel(database).daily(data_date), simulated=False
    )

    assert "## 二、保本目标" in report
    assert "Shopee：暂不可计算" in report

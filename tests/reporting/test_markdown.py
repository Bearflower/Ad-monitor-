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
    PlatformSummary,
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
    snapshot = ReportReadModel(database).daily(data_date)

    report = render_monthly_markdown([snapshot], month="2026-07")

    assert "# 广告月报 2026-07" in report
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


def test_daily_report_labels_supplier_fulfilled_inventory_as_not_applicable():
    snapshot = DailySnapshot(
        data_date=date(2026, 7, 29),
        platforms=(),
        sku_performance=(),
        alerts=(),
        recommendations=(),
        capabilities={
            "platform_metrics": "ready",
            "estimated_profit": "ready",
            "verified_profit": "ready",
            "inventory_safe_strategy": "not_applicable",
        },
    )

    report = render_daily_markdown(snapshot, simulated=False)

    assert "库存安全策略：不适用（货盘代发）" in report


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

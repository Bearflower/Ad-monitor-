from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.dashboard.views import (
    render_ad_funds_page,
    render_inventory_page,
    render_navigation,
    render_operating_statement_page,
    render_operations_forms,
    render_operations_page,
    render_optimization_center,
    render_optimization_page,
    render_profit_sharing_page,
)
from adwatch.domain import Platform
from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryService
from adwatch.ledger.models import ExpenseDraft
from adwatch.ledger.service import LedgerService
from adwatch.orders.models import PlatformOrderLine, PlatformSku
from adwatch.orders.repository import OrderRepository
from adwatch.pipeline.runner import PipelineRunner
from adwatch.profit_sharing.service import ProfitSharingService
from adwatch.storage.db import Database


def test_unified_navigation_and_forms_cover_core_business_modules():
    navigation = render_navigation("/")
    forms = render_operations_forms("csrf")

    for label in (
        "今日经营",
        "月度经营账",
        "广告调优",
        "收入与广告资金",
        "SKU与库存",
        "记账对账",
        "合伙人分润",
        "审批执行",
    ):
        assert label in navigation
    assert 'name="csrf_token" value="csrf"' in forms
    assert "费用／前期投入" in forms
    assert "资金性质" in forms
    assert 'action="/fulfillment"' in forms
    assert "货盘代发" in forms and "自有备货" in forms
    assert "记账对账" in render_operations_page("csrf")


def test_operating_statement_page_renders_twelve_months(tmp_path):
    database = Database(tmp_path / "statement-page.sqlite3")
    database.migrate()
    page = render_operating_statement_page(database, year=2026)
    assert "月度经营账" in page
    assert page.count('data-month="2026-') == 12
    assert "采购现金支出" in page
    assert "广告实际消耗" in page
    assert "广告充值不计入利润" in page
    assert "年度合计" in page


def test_operations_forms_expose_multicurrency_fields(tmp_path):
    database = Database(tmp_path / "operations.sqlite3")
    database.migrate()
    page = render_operations_page("secret", database, date(2026, 8, 12))
    for field in (
        "year_month",
        "rate_to_cny",
        "goods_cost_original",
        "service_fee_original",
        "entry_name",
        "credited_amount_original",
        "currency",
    ):
        assert f'name="{field}"' in page


def test_optimization_center_explains_roas_evidence_and_execution_state():
    page = render_optimization_center(
        platform_roas="4.20",
        net_sales_roas="3.50",
        profit_roas="1.40",
        confidence="inventory_safe",
        evidence=("退款已扣除", "库存可售 30"),
        action="reduce_budget",
        before="100",
        after="80",
        execution_status="shadow_ready",
    )
    for label in (
        "平台 ROAS",
        "净销售 ROAS",
        "利润 ROAS",
        "退款已扣除",
        "库存可售 30",
        "100 → 80",
        "shadow_ready",
    ):
        assert label in page


def test_optimization_page_gates_financial_roas_and_strategy_on_lineage(
    tmp_path,
):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "optimization-pending.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    AnalysisService(database).run(data_date)

    page = render_optimization_page(database, data_date)

    assert "平台 ROAS" in page
    assert "净销售 ROAS" in page and "利润 ROAS" in page
    assert page.count("待补数据") >= 2
    assert "ROAS 数据门禁" in page
    assert "退款金额" in page and "商品成本" in page
    assert "策略建议待补数据" in page


def test_optimization_page_shows_financial_roas_but_keeps_inventory_gate(
    tmp_path,
):
    data_date = date(2026, 7, 23)
    database = Database(tmp_path / "optimization-financial.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    analysis = AnalysisService(database)
    analysis.seed_mock_business_data(data_date)
    analysis.run(data_date)

    page = render_optimization_page(database, data_date)

    assert "ROAS 数据门禁：财务输入已就绪" in page
    assert "策略建议待补数据：平台库存快照" in page
    assert "N/A" not in page


def test_inventory_page_shows_missing_cost_and_in_page_write_forms(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "虾皮泰国", "1", "2", "SKU-A", "Blue",
                "Product A", 6, observed_at,
            ),
        )
    )
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "虾皮泰国", "260801ORDER", "1", "2",
                "SKU-A", "Blue", "Product A", 1, Decimal(100),
                "THB", "completed", "delivered", "",
                date(2026, 8, 1), observed_at,
            ),
        )
    )

    page = render_inventory_page(
        database,
        date(2026, 8, 1),
        csrf_token="inventory-csrf",
    )

    assert "SKU-A" in page
    assert 'action="/sku-costs"' in page
    assert 'action="/fulfillment"' in page
    assert 'action="/purchases"' in page
    assert 'name="csrf_token" value="inventory-csrf"' in page
    assert 'value="2026-08-01"' in page
    assert 'class="missing-cost-list"' in page
    assert page.count('class="missing-cost-item"') == 1
    assert 'class="order-cost-gap-item"' not in page
    assert '<summary class="missing-cost-summary">' in page
    assert 'class="missing-cost-editor"' in page
    assert 'class="missing-cost-table"' not in page
    assert "min-width:560px" not in page
    assert "overflow-wrap:anywhere" in page
    assert (
        '<label for="sku-cost-0-effective-date">成本生效日期</label>'
        in page
    )
    assert (
        '<label for="sku-cost-0-unit-cost">单位成本 (CNY)</label>'
        in page
    )
    assert '<label for="sku-cost-0-note">备注</label>' in page


def test_inventory_page_uses_platform_sku_count_when_manual_ledger_empty(tmp_path):
    database = Database(tmp_path / "platform-kpi.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed_at = datetime(2026, 8, 3, 9, tzinfo=UTC)
    repository.upsert_skus(
        tuple(
            PlatformSku(
                "shopee", "shop", f"item-{index}", f"model-{index}",
                f"SKU-{index}", "", f"Product {index}", index, observed_at,
            )
            for index in range(1, 325)
        )
    )

    page = render_inventory_page(
        database,
        date(2026, 8, 3),
        csrf_token="token",
    )

    assert "平台 SKU 数" in page
    assert ">324<" in page
    assert "手工台账 SKU" in page
    assert "当前库存 SKU 数" not in page


def test_inventory_page_shows_escaped_recalculation_result(tmp_path):
    database = Database(tmp_path / "recalculation-notice.sqlite3")
    database.migrate()

    page = render_inventory_page(
        database,
        date(2026, 8, 3),
        csrf_token="inventory-csrf",
        notice="重算已执行：仍有 6 条待补 <script>alert(1)</script>",
        notice_level="warning",
    )

    assert 'class="recalculation-notice warning"' in page
    assert 'role="status"' in page
    assert "重算已执行：仍有 6 条待补" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_dedicated_pages_render_closure_forms_with_real_ids_and_csrf(tmp_path):
    database = Database(tmp_path / "closure-views.sqlite3")
    database.migrate()
    ledger = LedgerService(database)
    expense = ledger.create_expense(
        ExpenseDraft(
            occurred_on=date(2026, 8, 4),
            category="<script>物流</script>",
            amount_original=Decimal(10),
            currency="CNY",
            rate_to_cny=Decimal(1),
            payer="洁云",
            fund_nature="operating_expense",
            affects_profit=True,
            affects_capital=False,
        ),
        actor="test",
    )
    inventory = InventoryService(database)
    inventory.receive_purchase(
        receipt_id="PO-REAL-ID",
        supplier="<img src=x onerror=alert(1)>",
        received_on=date(2026, 8, 4),
        lines=(PurchaseLine("SKU-1", 2, Decimal(5)),),
        actor="test",
    )
    sharing = ProfitSharingService(database)
    sharing.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal(1)},
        actor="test",
    )
    period = sharing.create_period(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 4),
        net_profit_cny=Decimal(100),
        actor="test",
    )

    ad_page = render_ad_funds_page(
        database, date(2026, 8, 4), csrf_token="csrf-value"
    )
    inventory_page = render_inventory_page(
        database, date(2026, 8, 4), csrf_token="csrf-value"
    )
    profit_page = render_profit_sharing_page(
        database, date(2026, 8, 4), csrf_token="csrf-value"
    )

    for page in (ad_page, inventory_page, profit_page):
        assert '<meta name="viewport"' in page
        assert 'name="csrf_token" value="csrf-value"' in page
        assert 'name="reason"' in page
        assert 'enctype="multipart/form-data"' in page
    assert 'action="/expenses/confirm"' in ad_page
    assert f'value="{expense.id}"' in ad_page
    assert "<script>物流</script>" not in ad_page
    assert 'action="/inventory/adjust"' in inventory_page
    assert 'action="/purchases/reverse"' in inventory_page
    assert 'value="PO-REAL-ID"' in inventory_page
    assert "<img src=x onerror=alert(1)>" not in inventory_page
    assert 'action="/profit-periods/confirm"' in profit_page
    assert f'value="{period}"' in profit_page
    assert 'action="/profit-payments/reverse"' in profit_page

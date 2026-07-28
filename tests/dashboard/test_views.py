from adwatch.dashboard.views import (
    render_navigation,
    render_operations_forms,
    render_operations_page,
    render_optimization_center,
)


def test_unified_navigation_and_forms_cover_core_business_modules():
    navigation = render_navigation("/")
    forms = render_operations_forms("csrf")

    for label in (
        "今日经营",
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
    assert "记账对账" in render_operations_page("csrf")


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

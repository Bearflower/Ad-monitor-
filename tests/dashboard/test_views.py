from adwatch.dashboard.views import render_navigation, render_operations_forms


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

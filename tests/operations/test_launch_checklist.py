from adwatch.operations.launch_checklist import (
    LaunchReadiness,
    build_launch_checklist,
)


def test_launch_checklist_includes_every_external_gate():
    readiness = LaunchReadiness()

    items = build_launch_checklist(readiness)

    assert {item.code for item in items} == {
        "ziniao_bridge",
        "tiktok_campaign_validation",
        "shopee_campaign_validation",
        "business_costs",
        "sku_mapping",
        "refund_source",
        "inventory_source",
        "exchange_rate_source",
        "feishu_callback",
        "shadow_reconciliation",
        "rollback_drill",
        "selector_activation",
        "platform_api_oauth",
        "three_day_reconciliation",
        "live_allowlist",
    }


def test_launch_checklist_keeps_only_unresolved_external_items():
    readiness = LaunchReadiness(
        ziniao_bridge=True,
        feishu_callback=True,
    )

    items = build_launch_checklist(readiness)

    assert "ziniao_bridge" not in {item.code for item in items}
    assert "feishu_callback" not in {item.code for item in items}
    assert "business_costs" in {item.code for item in items}

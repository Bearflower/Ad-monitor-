from adwatch.operations.launch_checklist import build_launch_checklist


def test_launch_checklist_keeps_only_unresolved_external_items():
    items = build_launch_checklist(
        bridge_ready=True,
        tiktok_campaign_ready=False,
        business_costs_ready=False,
        callback_ready=True,
        shadow_reconciled=False,
        rollback_drilled=False,
        live_allowlist_ready=False,
    )

    assert [item.code for item in items] == [
        "tiktok_campaign_validation",
        "business_costs",
        "shadow_reconciliation",
        "rollback_drill",
        "live_allowlist",
    ]

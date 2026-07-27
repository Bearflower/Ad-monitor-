from adwatch.operations.readiness import ReadinessCheck, readiness_status


def test_readiness_distinguishes_external_and_data_dependencies():
    checks = readiness_status(
        bridge_ready=False,
        has_tiktok_campaign=False,
        has_business_costs=False,
        feishu_callback_ready=False,
    )

    assert ReadinessCheck("ziniao_bridge", "pending_external") in checks
    assert ReadinessCheck("business_costs", "pending_data") in checks
    assert ReadinessCheck("live_writes", "blocked") in checks

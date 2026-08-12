from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str


def readiness_status(
    *,
    bridge_ready: bool,
    has_tiktok_campaign: bool,
    has_business_costs: bool,
    feishu_callback_ready: bool,
    has_shopee_campaign: bool = False,
    tiktok_enabled: bool = False,
    feishu_callback_enabled: bool = False,
    live_writes_enabled: bool = False,
    production_publish_ready: bool = False,
) -> tuple[ReadinessCheck, ...]:
    return (
        ReadinessCheck(
            "ziniao_bridge", "ready" if bridge_ready else "pending_external"
        ),
        ReadinessCheck(
            "tiktok_campaign",
            "disabled"
            if not tiktok_enabled
            else "ready"
            if has_tiktok_campaign
            else "pending_data",
        ),
        ReadinessCheck(
            "shopee_campaign",
            "ready" if has_shopee_campaign else "pending_data",
        ),
        ReadinessCheck(
            "business_costs",
            "ready" if has_business_costs else "pending_data",
        ),
        ReadinessCheck(
            "feishu_callback",
            "disabled"
            if not feishu_callback_enabled
            else "ready"
            if feishu_callback_ready
            else "pending_external",
        ),
        ReadinessCheck(
            "production_publish",
            "ready" if production_publish_ready else "pending_external",
        ),
        ReadinessCheck(
            "live_writes", "ready" if live_writes_enabled else "disabled"
        ),
    )

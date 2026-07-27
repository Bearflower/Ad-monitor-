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
) -> tuple[ReadinessCheck, ...]:
    return (
        ReadinessCheck(
            "ziniao_bridge", "ready" if bridge_ready else "pending_external"
        ),
        ReadinessCheck(
            "tiktok_campaign",
            "ready" if has_tiktok_campaign else "pending_data",
        ),
        ReadinessCheck(
            "business_costs",
            "ready" if has_business_costs else "pending_data",
        ),
        ReadinessCheck(
            "feishu_callback",
            "ready" if feishu_callback_ready else "pending_external",
        ),
        ReadinessCheck("live_writes", "blocked"),
    )

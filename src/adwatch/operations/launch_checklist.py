from dataclasses import dataclass


@dataclass(frozen=True)
class LaunchItem:
    code: str
    description: str


def build_launch_checklist(
    *,
    bridge_ready: bool,
    tiktok_campaign_ready: bool,
    business_costs_ready: bool,
    callback_ready: bool,
    shadow_reconciled: bool,
    rollback_drilled: bool,
    live_allowlist_ready: bool,
) -> tuple[LaunchItem, ...]:
    candidates = (
        (
            bridge_ready,
            LaunchItem("ziniao_bridge", "启动并保持紫鸟 Bridge 可用"),
        ),
        (
            tiktok_campaign_ready,
            LaunchItem(
                "tiktok_campaign_validation",
                "使用有数据的 TikTok Campaign 完成现场验收",
            ),
        ),
        (
            business_costs_ready,
            LaunchItem("business_costs", "导入极简经营成本"),
        ),
        (
            callback_ready,
            LaunchItem("feishu_callback", "配置飞书公网 HTTPS 回调"),
        ),
        (
            shadow_reconciled,
            LaunchItem("shadow_reconciliation", "完成 Shadow 人工对账"),
        ),
        (
            rollback_drilled,
            LaunchItem("rollback_drill", "完成写操作回滚演练"),
        ),
        (
            live_allowlist_ready,
            LaunchItem("live_allowlist", "配置 Live 店铺和 Campaign 允许清单"),
        ),
    )
    return tuple(item for ready, item in candidates if not ready)


def render_launch_checklist(items: tuple[LaunchItem, ...]) -> str:
    if not items:
        return "# Adwatch 上线待办\n\n无待办"
    lines = ["# Adwatch 上线待办", ""]
    lines.extend(f"- [ ] `{item.code}`：{item.description}" for item in items)
    return "\n".join(lines)

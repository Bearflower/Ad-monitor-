from dataclasses import dataclass


@dataclass(frozen=True)
class LaunchItem:
    code: str
    description: str
    optional: bool = False


@dataclass(frozen=True)
class LaunchReadiness:
    ziniao_bridge: bool = False
    tiktok_campaign_validation: bool = False
    shopee_campaign_validation: bool = False
    business_costs: bool = False
    sku_mapping: bool = False
    refund_source: bool = False
    inventory_source: bool = False
    exchange_rate_source: bool = False
    feishu_callback: bool = False
    shadow_reconciliation: bool = False
    rollback_drill: bool = False
    selector_activation: bool = False
    platform_api_oauth: bool = False
    three_day_reconciliation: bool = False
    live_allowlist: bool = False


DESCRIPTIONS = {
    "ziniao_bridge": "启动并保持紫鸟 Bridge 可用",
    "tiktok_campaign_validation": "使用有数据的 TikTok Campaign 完成现场验收",
    "shopee_campaign_validation": "完成 Shopee Campaign 读取现场复验",
    "business_costs": "导入真实经营成本",
    "sku_mapping": "建立平台商品 ID 与 ERP SKU 映射",
    "refund_source": "配置真实退款数据源",
    "inventory_source": "配置真实库存数据源",
    "exchange_rate_source": "配置每日汇率数据源",
    "feishu_callback": "配置飞书公网 HTTPS 回调",
    "shadow_reconciliation": "完成 Shadow 人工对账",
    "rollback_drill": "完成写操作回滚演练",
    "selector_activation": "逐平台、逐动作完成页面选择器现场激活",
    "platform_api_oauth": "按需配置 TikTok/Shopee 官方 API OAuth",
    "three_day_reconciliation": "连续三天全链路对账准确率达到 99%",
    "live_allowlist": "配置 Live 店铺和 Campaign 精确允许清单",
}


def build_launch_checklist(
    readiness: LaunchReadiness,
) -> tuple[LaunchItem, ...]:
    items = []
    for code, description in DESCRIPTIONS.items():
        if not getattr(readiness, code):
            items.append(
                LaunchItem(
                    code,
                    description,
                    optional=code == "platform_api_oauth",
                )
            )
    return tuple(items)


def render_launch_checklist(items: tuple[LaunchItem, ...]) -> str:
    if not items:
        return "# Adwatch 上线待办\n\n无待办"
    lines = ["# Adwatch 上线待办", ""]
    lines.extend(
        f"- [ ] `{item.code}`"
        f"{'（可选）' if item.optional else ''}：{item.description}"
        for item in items
    )
    return "\n".join(lines)

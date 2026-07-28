from dataclasses import dataclass
from decimal import Decimal

from adwatch.reporting.read_model import DailySnapshot, PlatformSummary


@dataclass(frozen=True)
class DailyReportPresentation:
    markdown: str
    risk_label: str
    header_template: str


PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "shopee": "Shopee",
}
CAPABILITY_LABELS = {
    "platform_metrics": "平台广告数据",
    "estimated_profit": "估算利润",
    "verified_profit": "已验证利润",
    "inventory_safe_strategy": "库存安全策略",
}
STATUS_LABELS = {
    "ready": "已就绪",
    "pending_data": "待补数据",
    "pending_external": "等待外部配置",
    "blocked": "已阻止",
    "proposed": "待审批",
    "approved": "已批准",
    "rejected": "已拒绝",
    "executed": "已执行",
}
ACTION_LABELS = {
    "increase_budget": "增加预算",
    "reduce_budget": "降低预算",
    "adjust_roas_target": "调整目标 ROAS",
    "pause": "暂停广告",
    "resume": "恢复广告",
    "allocate_retest": "分配商品复测预算",
}
MESSAGE_LABELS = {
    "Spend increased by more than 30%": "广告花费较基线增长超过 30%",
}
REASON_LABELS = {
    "ROAS stayed below 50% of target for three days":
        "ROAS 连续三天低于目标值的 50%",
    "ROAS is below 70% of target after learning":
        "学习期结束后，ROAS 低于目标值的 70%",
    "ROAS remained below 80% of target after learning":
        "学习期结束后，ROAS 持续低于目标值的 80%",
    "ROAS exceeded 150% of target after learning":
        "学习期结束后，ROAS 高于目标值的 150%",
    "ROAS is on target with profit and sufficient stock":
        "ROAS 达标、利润为正且库存充足",
    "Verified product candidate uses no more than 20% of the budget pool":
        "已验证候选商品的复测预算不超过预算池的 20%",
}


def _platform_line(snapshot: DailySnapshot, platform: str) -> str:
    item = next(
        (summary for summary in snapshot.platforms if summary.platform == platform),
        None,
    )
    if item is None:
        return "暂无数据"
    roas = "不可计算" if item.roas is None else f"{item.roas:.2f}"
    profit = (
        "利润待补数据"
        if item.net_profit is None
        else f"净利润 {_cny(item.net_profit)}"
    )
    return (
        f"消耗 {item.spend:.2f} / GMV {item.gmv:.2f} / "
        f"ROAS {roas} / 订单 {item.orders} / {profit}"
    )


def _cny(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}¥{abs(value):.2f}"


def _profit_lines(item: PlatformSummary) -> tuple[str, ...]:
    label = PLATFORM_LABELS.get(item.platform, item.platform)
    if item.attributed_sales_cny is None:
        return (f"- {label}：利润拆解待补数据",)
    return (
        f"- {label} 广告归因销售额：{_cny(item.attributed_sales_cny)}",
        (
            f"- {label} SKU 成本及其他费用："
            f"{_cny(item.sku_and_other_cost_cny)}"
        ),
        f"- {label} 平台综合费用：{_cny(item.platform_fee_cny)}",
        f"- {label} 广告费用：{_cny(item.ad_spend_cny)}",
        f"- {label} 净利润：{_cny(item.net_profit)}",
    )


def _risk(snapshot: DailySnapshot) -> tuple[str, str, str]:
    if any(
        item.net_profit is not None and item.net_profit < 0
        for item in snapshot.platforms
    ) or any(
        item.get("severity") in {"critical", "error"}
        for item in snapshot.alerts
    ):
        return "🔴", "高风险", "red"
    if any(item.get("severity") == "warning" for item in snapshot.alerts):
        return "🟠", "中风险", "orange"
    if any(status != "ready" for status in snapshot.capabilities.values()):
        return "🟡", "待关注", "yellow"
    return "🟢", "正常", "green"


def _alert_lines(snapshot: DailySnapshot) -> tuple[str, ...]:
    if not snapshot.alerts:
        return ("- 🟢 暂无异常",)
    severity_icons = {
        "critical": "🔴",
        "error": "🔴",
        "warning": "🟠",
        "info": "🟡",
    }
    return tuple(
        f"- {severity_icons.get(item.get('severity', ''), '🟡')} "
        f"{MESSAGE_LABELS.get(item['message'], item['message'])}"
        for item in snapshot.alerts
    )


def _recommendation_lines(snapshot: DailySnapshot) -> tuple[str, ...]:
    if not snapshot.recommendations:
        return (
            "- 建议：暂不调整",
            "- 原因：尚未满足学习期、连续观察窗口和安全门禁的动作条件",
        )
    lines = []
    for item in snapshot.recommendations:
        lines.extend(
            (
                f"- 建议：{ACTION_LABELS.get(item['action'], item['action'])}",
                f"- 原因：{REASON_LABELS.get(item['reason'], item['reason'])}",
                (
                    f"- 状态："
                    f"{STATUS_LABELS.get(item['status'], item['status'])}"
                ),
            )
        )
    return tuple(lines)


def present_daily_report(
    snapshot: DailySnapshot, *, simulated: bool
) -> DailyReportPresentation:
    marker = "模拟数据" if simulated else "真实数据"
    risk_icon, risk_label, header_template = _risk(snapshot)
    profit_lines = tuple(
        line for item in snapshot.platforms for line in _profit_lines(item)
    )
    capabilities = tuple(
        f"- {CAPABILITY_LABELS.get(name, name)}："
        f"{STATUS_LABELS.get(status, status)}"
        for name, status in snapshot.capabilities.items()
    )
    markdown = "\n".join(
        (
            f"# 广告经营日报｜{snapshot.data_date.isoformat()}【{marker}】",
            "",
            f"**风险等级：{risk_icon} {risk_label}**",
            "",
            "## 一、核心经营结果",
            *(profit_lines or ("- 暂无利润数据",)),
            "",
            "## 二、平台表现",
            f"- TikTok：{_platform_line(snapshot, 'tiktok')}",
            f"- Shopee：{_platform_line(snapshot, 'shopee')}",
            "",
            "## 三、异常与风险",
            *_alert_lines(snapshot),
            "",
            "## 四、建议动作",
            *_recommendation_lines(snapshot),
            "",
            "## 五、数据可信度",
            *capabilities,
        )
    )
    return DailyReportPresentation(markdown, risk_label, header_template)


def render_daily_markdown(
    snapshot: DailySnapshot, *, simulated: bool
) -> str:
    return present_daily_report(snapshot, simulated=simulated).markdown


def render_weekly_markdown(snapshots: list[DailySnapshot]) -> str:
    if not snapshots:
        return "# 广告周报\n\n无数据"
    return "\n\n".join(
        ["# 广告周报"]
        + [
            render_daily_markdown(snapshot, simulated=False)
            for snapshot in snapshots
        ]
    )


def render_monthly_markdown(
    snapshots: list[DailySnapshot], *, month: str
) -> str:
    if not snapshots:
        return f"# 广告月报 {month}\n\n无数据"
    platform_totals: dict[str, dict[str, object]] = {}
    for snapshot in snapshots:
        for item in snapshot.platforms:
            totals = platform_totals.setdefault(
                item.platform,
                {
                    "spend": 0,
                    "gmv": 0,
                    "orders": 0,
                },
            )
            totals["spend"] += item.spend
            totals["gmv"] += item.gmv
            totals["orders"] += item.orders
    lines = [f"# 广告月报 {month}", ""]
    for platform, totals in sorted(platform_totals.items()):
        label = PLATFORM_LABELS.get(platform, platform)
        spend = totals["spend"]
        gmv = totals["gmv"]
        roas = "不可计算" if not spend else f"{gmv / spend:.2f}"
        lines.append(
            f"- {label}：消耗 {spend:.2f} / GMV {gmv:.2f} / "
            f"ROAS {roas} / 订单 {totals['orders']}"
        )
    return "\n".join(lines)

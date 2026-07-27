from adwatch.reporting.read_model import DailySnapshot


def _platform_line(snapshot: DailySnapshot, platform: str) -> str:
    item = next(
        (summary for summary in snapshot.platforms if summary.platform == platform),
        None,
    )
    if item is None:
        return "无数据"
    roas = "N/A" if item.roas is None else f"{item.roas:.2f}"
    profit = (
        "利润待补数据"
        if item.net_profit is None
        else f"净利润(CNY) {item.net_profit:.2f}"
    )
    return (
        f"消耗 {item.spend:.2f} / GMV {item.gmv:.2f} / "
        f"ROAS {roas} / 订单 {item.orders} / {profit}"
    )


def render_daily_markdown(
    snapshot: DailySnapshot, *, simulated: bool
) -> str:
    marker = "【模拟数据】" if simulated else "【真实数据】"
    top = snapshot.sku_performance[:3]
    bottom = tuple(reversed(snapshot.sku_performance[-3:]))
    alerts = (
        "\n".join(f"- {item['message']}" for item in snapshot.alerts)
        if snapshot.alerts
        else "- 无"
    )
    rankings = "\n".join(
        f"- {label} {item.platform}/{item.sku_id}: "
        f"ROAS {'N/A' if item.roas is None else f'{item.roas:.2f}'}"
        for label, values in (("TOP", top), ("BOTTOM", bottom))
        for item in values
    )
    capabilities = "\n".join(
        f"- {name}: {status}"
        for name, status in snapshot.capabilities.items()
    )
    return "\n".join(
        (
            f"# 广告每日快报 {snapshot.data_date.isoformat()} {marker}",
            "",
            f"【TikTok】{_platform_line(snapshot, 'tiktok')}",
            "",
            f"【Shopee】{_platform_line(snapshot, 'shopee')}",
            "",
            "【异常告警】",
            alerts,
            "",
            "【TOP3/BOTTOM3】",
            rankings or "- 无",
            "",
            "【经营分析可信度】",
            capabilities,
            "",
            f"待审批建议：{len(snapshot.recommendations)} 条",
        )
    )


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
        label = platform.capitalize()
        spend = totals["spend"]
        gmv = totals["gmv"]
        roas = "N/A" if not spend else f"{gmv / spend:.2f}"
        lines.append(
            f"- {label}: 消耗 {spend:.2f} / GMV {gmv:.2f} / "
            f"ROAS {roas} / 订单 {totals['orders']}"
        )
    return "\n".join(lines)

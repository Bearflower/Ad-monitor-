from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from adwatch.reporting.read_model import (
    DailySnapshot,
    PlatformSummary,
    ReportReadModel,
)
from adwatch.storage.db import Database


@dataclass(frozen=True)
class DailyReportPresentation:
    markdown: str
    risk_label: str
    header_template: str


PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "shopee": "Shopee",
}
CURRENCY_LABELS = {
    "tiktok": "THB",
    "shopee": "THB",
}
BREAK_EVEN_CONFIDENCE_LABELS = {
    "verified": "已验证",
    "reconciliation_pending": "待对账",
    "missing_data": "待补数据",
}
CAPABILITY_LABELS = {
    "platform_metrics": "平台广告数据",
    "estimated_profit": "估算利润",
    "verified_profit": "已验证利润",
    "inventory_safe_strategy": "库存安全策略",
}
STATUS_LABELS = {
    "ready": "已就绪",
    "carried_forward": "沿用已有数据",
    "partial": "部分就绪",
    "pending_data": "待补数据",
    "pending_external": "等待外部配置",
    "blocked": "已阻止",
    "ambiguous": "口径不明确",
    "not_applicable": "不适用",
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
FEE_SELECTION_LABELS = {
    "covering_period": "周期覆盖",
    "carried_forward": "沿用最近周期",
    "legacy_cost_rate": "历史成本费率",
}
INPUT_LABELS = {
    "gmv": "广告归因销售额",
    "refunds": "退款金额",
    "commission_rate": "平台费率",
    "product_cost": "商品成本",
    "ad_spend": "广告花费",
    "seller_shipping": "卖家运费",
    "coupons": "优惠券",
    "allocated_fixed_cost": "分摊固定成本",
    "exchange_rate": "汇率",
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


def _cny(value: Decimal | None) -> str:
    if value is None:
        return "待补数据"
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


def _break_even_lines(item: PlatformSummary) -> tuple[str, ...]:
    label = PLATFORM_LABELS.get(item.platform, item.platform)
    currency = CURRENCY_LABELS.get(item.platform, "平台原币")
    target = item.break_even_target
    if target is None or target.break_even_roas is None:
        explanation = "" if target is None else f"（{target.explanation}）"
        return (f"- {label}：暂不可计算{explanation}",)
    if target.gmv_gap and target.gmv_gap > 0:
        distance = (
            f"还差 {currency} {target.gmv_gap:.2f} / "
            f"约 {target.order_gap} 单"
        )
    else:
        distance = "已达到经营保本线"
    return (
        f"- {label} 当前 ROAS：{item.roas:.2f}",
        f"- {label} 经营保本 ROAS：{target.break_even_roas:.2f}",
        (
            f"- 当前花费下保本 GMV："
            f"{currency} {target.break_even_gmv:.2f}"
        ),
        f"- 当前客单价：{currency} {target.average_order_value:.2f}",
        f"- 估算保本单量：约 {target.break_even_orders} 单",
        f"- 距离保本：{distance}",
        (
            f"- 数据可信度："
            f"{BREAK_EVEN_CONFIDENCE_LABELS[target.confidence]}"
        ),
        f"- 说明：{target.explanation}",
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


def _fee_rate_lines(snapshot: DailySnapshot) -> tuple[str, ...]:
    if not snapshot.fee_rates:
        return ("- 平台费率：待补数据",)
    lines = []
    for item in snapshot.fee_rates:
        label = PLATFORM_LABELS.get(item.platform, item.platform)
        if item.effective_rate is None:
            lines.append(f"- {label} / {item.store}：待补数据")
            continue
        selection = FEE_SELECTION_LABELS.get(
            item.selection, item.selection
        )
        details = [f"{item.effective_rate * 100:.4f}%", selection]
        if item.period_start is not None and item.period_end is not None:
            details.insert(
                1,
                (
                    f"{item.period_start.isoformat()} 至 "
                    f"{item.period_end.isoformat()}"
                ),
            )
        if item.source_name:
            details.append(item.source_name)
        lines.append(f"- {label} / {item.store}：{'｜'.join(details)}")
    return tuple(lines)


def _profit_source_lines(snapshot: DailySnapshot) -> tuple[str, ...]:
    if not snapshot.profit_sources:
        return ("- 暂无可追溯的利润来源",)
    lines = []
    for item in snapshot.profit_sources:
        label = PLATFORM_LABELS.get(item.platform, item.platform)
        profit_status = (
            "利润已就绪"
            if item.profit_status == "ready"
            else "利润部分就绪"
            if item.profit_status == "partial"
            else "利润待补数据"
        )
        inventory_date = (
            item.inventory_snapshot_date.isoformat()
            if item.inventory_snapshot_date is not None
            else "待补数据"
        )
        exchange_date = (
            item.exchange_source_date.isoformat()
            if item.exchange_source_date is not None
            else "待补数据"
        )
        fee_period = (
            f"{item.fee_period_start.isoformat()} 至 "
            f"{item.fee_period_end.isoformat()}"
            if item.fee_period_start is not None
            and item.fee_period_end is not None
            else "待补数据"
        )
        fee_selection = FEE_SELECTION_LABELS.get(
            item.fee_selection, item.fee_selection
        )
        missing = (
            "、".join(
                INPUT_LABELS.get(name, name) for name in item.missing_inputs
            )
            if item.missing_inputs
            else "无"
        )
        lines.extend(
            (
                f"- {label} / {item.store}：{profit_status}",
                (
                    f"  - 广告指标 {item.metric_rows} 行 / "
                    f"归因订单 {item.attributed_orders} 单 / "
                    f"平台订单 {item.platform_orders} 单"
                ),
                (
                    f"  - 成本覆盖：{item.confirmed_cost_orders}/"
                    f"{item.platform_orders} 个平台订单"
                ),
                (
                    f"  - 退款金额观测：{item.refund_amount_observations}/"
                    f"{item.refund_observations} 个退款观测"
                ),
                f"  - 库存快照：{inventory_date}",
                (
                    f"  - 汇率来源：{exchange_date}"
                    f"（{item.exchange_resolution_status}）"
                ),
                f"  - 结算费率周期：{fee_period}（{fee_selection}）",
                f"  - 关键缺口：{missing}",
            )
        )
        for lineage in snapshot.profit_input_lineage:
            if lineage.platform != item.platform or lineage.store != item.store:
                continue
            input_label = INPUT_LABELS.get(
                lineage.input_name, lineage.input_name
            )
            status = STATUS_LABELS.get(lineage.status, lineage.status)
            source_date = (
                lineage.source_date.isoformat()
                if lineage.source_date is not None
                else "无来源日期"
            )
            lines.append(
                f"  - {input_label}：{status}｜{lineage.source_type}｜"
                f"{source_date}｜{lineage.covered_rows}/"
                f"{lineage.total_rows} 行"
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
    break_even_lines = tuple(
        line for item in snapshot.platforms for line in _break_even_lines(item)
    )
    capabilities = tuple(
        f"- {CAPABILITY_LABELS.get(name, name)}："
        f"{STATUS_LABELS.get(status, status)}"
        for name, status in snapshot.capabilities.items()
    )
    fee_rates = _fee_rate_lines(snapshot)
    profit_sources = _profit_source_lines(snapshot)
    markdown = "\n".join(
        (
            f"# 广告经营日报｜{snapshot.data_date.isoformat()}【{marker}】",
            "",
            f"**风险等级：{risk_icon} {risk_label}**",
            "",
            "## 一、核心经营结果",
            *(profit_lines or ("- 暂无利润数据",)),
            "",
            "## 二、保本目标",
            *(break_even_lines or ("- 暂无数据",)),
            "",
            "## 三、平台表现",
            f"- TikTok：{_platform_line(snapshot, 'tiktok')}",
            f"- Shopee：{_platform_line(snapshot, 'shopee')}",
            "",
            "## 四、异常与风险",
            *_alert_lines(snapshot),
            "",
            "## 五、建议动作",
            *_recommendation_lines(snapshot),
            "",
            "## 六、数据可信度",
            *capabilities,
            "",
            "## 七、平台费率来源",
            *fee_rates,
            "",
            "## 八、数据来源与新鲜度",
            *profit_sources,
        )
    )
    return DailyReportPresentation(markdown, risk_label, header_template)


def render_daily_markdown(
    snapshot: DailySnapshot, *, simulated: bool
) -> str:
    return present_daily_report(snapshot, simulated=simulated).markdown


def _pct(value: Decimal, total: Decimal) -> str:
    """计算占比百分比。"""
    if total == 0:
        return "-"
    return f"{float(value / total * 100):.1f}%"


def _format_mom(mom_value: str | None) -> str:
    """格式化环比字符串，None 时显示为 '-'。"""
    return mom_value if mom_value is not None else "-"


def _severity_label(severity: str) -> str:
    labels = {
        "critical": "严重",
        "error": "错误",
        "warning": "警告",
        "info": "提示",
    }
    return labels.get(severity, severity)


def _action_label(action: str) -> str:
    labels = {
        "increase_budget": "提价建议",
        "reduce_budget": "降价建议",
        "adjust_roas_target": "调整 ROAS 目标",
        "pause": "暂停建议",
        "resume": "恢复建议",
        "allocate_retest": "复测预算分配",
        "approved": "已审批",
    }
    return labels.get(action, action)


def render_weekly_markdown(database: Database, week_end: date) -> str:
    """生成周报 markdown。

    调用 ReportReadModel.weekly() 获取聚合数据，渲染为可读的 Markdown 报告。
    """
    read_model = ReportReadModel(database)
    data = read_model.weekly(week_end)
    start = data["period_start"]
    end = data["period_end"]
    t = data["totals"]
    mom = data["mom"]

    lines = [
        f"# 广告周报｜{start.isoformat()} 至 {end.isoformat()}",
        "",
        "## 本周汇总",
        "| 指标 | 本周 | 环比 |",
        "|------|------|------|",
        (
            f"| 广告消耗 | {_cny(t['ad_spend_cny'])} "
            f"| {_format_mom(mom['spend'])} |"
        ),
        (
            f"| GMV | {_cny(t['gmv'])} "
            f"| {_format_mom(mom['gmv'])} |"
        ),
        (
            f"| 净利润 | {_cny(t['net_profit'])} "
            f"| {_format_mom(mom['net_profit'])} |"
        ),
        "",
        "## 平台表现",
    ]

    for p in data["platforms"]:
        label = PLATFORM_LABELS.get(p["platform"], p["platform"])
        roas = "不可计算" if p["roas"] is None else f"{p['roas']:.2f}"
        profit = (
            "利润待补数据"
            if p["net_profit"] is None
            else f"净利润 {_cny(p['net_profit'])}"
        )
        lines.append(
            f"- {label}：消耗 {p['spend']:.2f} / GMV {p['gmv']:.2f} / "
            f"ROAS {roas} / 订单 {p['orders']} / {profit}"
        )

    lines.extend([
        "",
        "## 每日趋势",
        "| 日期 | 消耗 | GMV | 利润 |",
        "|------|------|-----|------|",
    ])
    for point in data["daily_trends"]:
        profit_str = (
            _cny(point.net_profit)
            if point.net_profit is not None
            else "待补数据"
        )
        lines.append(
            f"| {point.data_date.isoformat()} | "
            f"{point.spend:.2f} | {point.gmv:.2f} | {profit_str} |"
        )

    lines.extend([
        "",
        "## 异常告警",
    ])
    if data["alerts"]:
        for severity, count in sorted(data["alerts"].items()):
            lines.append(
                f"- [{_severity_label(severity)}]：共 {count} 次"
            )
    else:
        lines.append("- 暂无异常告警")

    lines.extend([
        "",
        "## 策略建议",
    ])
    if data["recommendations"]:
        for action, count in sorted(data["recommendations"].items()):
            lines.append(f"- {_action_label(action)}：{count} 条")
    else:
        lines.append("- 暂无建议")

    lines.extend([
        "",
        "## 采集质量",
    ])
    c = data["collection"]
    lines.append(
        f"- 成功：{c['success_days']}/{c['total_days']} 天"
    )
    lines.append(
        f"- 异常：{c['failed_runs']} 次"
    )

    return "\n".join(lines)


def render_monthly_markdown(database: Database, year: int, month: int) -> str:
    """生成月报 markdown。

    调用 ReportReadModel.monthly() 获取聚合数据，渲染为可读的 Markdown 报告。
    """
    read_model = ReportReadModel(database)
    data = read_model.monthly(year, month)
    t = data["totals"]
    mom = data["mom"]

    lines = [
        f"# 广告月报｜{year}年{month:02d}月",
        "",
        "## 本周汇总",
        "| 指标 | 本月 | 环比 |",
        "|------|------|------|",
        (
            f"| 广告消耗 | {_cny(t['ad_spend_cny'])} "
            f"| {_format_mom(mom['spend'])} |"
        ),
        (
            f"| GMV | {_cny(t['gmv'])} "
            f"| {_format_mom(mom['gmv'])} |"
        ),
        (
            f"| 净利润 | {_cny(t['net_profit'])} "
            f"| {_format_mom(mom['net_profit'])} |"
        ),
        "",
        "## 经营总览",
        "| 项目 | 金额(CNY) | 占比 |",
        "|------|----------|------|",
    ]

    sales = t.get("attributed_sales_cny")
    if sales is not None and sales > 0:
        fee = t.get("platform_fee_cny", Decimal(0))
        ad = t.get("ad_spend_cny", Decimal(0))
        sku = t.get("sku_and_other_cost_cny", Decimal(0))
        profit = t.get("net_profit", Decimal(0))
        if fee is None:
            fee = Decimal(0)
        if ad is None:
            ad = Decimal(0)
        if sku is None:
            sku = Decimal(0)
        if profit is None:
            profit = Decimal(0)

        lines.append(f"| 销售收入 | {_cny(sales)} | 100% |")
        lines.append(f"| 平台佣金 | {_cny(fee)} | {_pct(fee, sales)} |")
        lines.append(f"| SKU 成本及其他 | {_cny(sku)} | {_pct(sku, sales)} |")
        lines.append(f"| 广告费用 | {_cny(ad)} | {_pct(ad, sales)} |")
        lines.append(f"| 净利润 | {_cny(profit)} | {_pct(profit, sales)} |")
    else:
        lines.append("| 销售收入 | 待补数据 | - |")

    lines.extend([
        "",
        "## 平台表现",
    ])
    for p in data["platforms"]:
        label = PLATFORM_LABELS.get(p["platform"], p["platform"])
        roas = "不可计算" if p["roas"] is None else f"{p['roas']:.2f}"
        profit = (
            "利润待补数据"
            if p["net_profit"] is None
            else f"净利润 {_cny(p['net_profit'])}"
        )
        lines.append(
            f"- {label}：消耗 {p['spend']:.2f} / GMV {p['gmv']:.2f} / "
            f"ROAS {roas} / 订单 {p['orders']} / {profit}"
        )

    lines.extend([
        "",
        "## 每周趋势",
        "| 周起始 | 消耗 | GMV | 利润 |",
        "|--------|------|-----|------|",
    ])
    for point in data["weekly_trends"]:
        profit_str = (
            _cny(point.net_profit)
            if point.net_profit is not None
            else "待补数据"
        )
        lines.append(
            f"| {point.data_date.isoformat()} | "
            f"{point.spend:.2f} | {point.gmv:.2f} | {profit_str} |"
        )

    lines.extend([
        "",
        "## 费用明细",
        "| 类别 | 金额(CNY) |",
        "|------|----------|",
    ])
    if data["expenses"]:
        for expense in data["expenses"]:
            lines.append(
                f"| {expense['category']} | {_cny(expense['amount_cny'])} |"
            )
    else:
        lines.append("| 暂无费用数据 | - |")

    lines.extend([
        "",
        "## 异常告警",
    ])
    if data["alerts"]:
        for severity, count in sorted(data["alerts"].items()):
            lines.append(
                f"- [{_severity_label(severity)}]：共 {count} 次"
            )
    else:
        lines.append("- 暂无异常告警")

    lines.extend([
        "",
        "## 策略建议",
    ])
    if data["recommendations"]:
        for action, count in sorted(data["recommendations"].items()):
            lines.append(f"- {_action_label(action)}：{count} 条")
    else:
        lines.append("- 暂无建议")

    lines.extend([
        "",
        "## 保本分析",
    ])
    be = data["break_even"]
    lines.append(
        f"- 月均保本 ROAS：{be['avg_break_even_roas']:.2f}"
        if be["avg_break_even_roas"] is not None
        else "- 月均保本 ROAS：待补数据"
    )
    lines.append(f"- 健康商品：{be['healthy_skus']} 个")
    lines.append(f"- 亏损商品：{be['losing_skus']} 个")

    return "\n".join(lines)

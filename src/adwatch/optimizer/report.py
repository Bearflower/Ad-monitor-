"""Shopee 广告优化本地 Markdown 日报。"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path

from adwatch.optimizer.models import (
    Action,
    AdMetric,
    Feedback,
    Recommendation,
    RecommendationSlot,
)
from adwatch.optimizer.repository import CollectionDay, OptimizerRepository

_ACTION_LABELS = {
    Action.INCREASE_BUDGET: "增加日预算",
    Action.DECREASE_BUDGET: "降低日预算",
    Action.DECREASE_TARGET_ROAS: "降低目标 ROAS",
    Action.INCREASE_TARGET_ROAS: "提高目标 ROAS",
    Action.HOLD: "保持并观察",
    Action.PAUSE: "暂停活动",
    Action.RESUME: "恢复活动",
    Action.CREATE_GROUP: "新建扩组",
    Action.SPLIT_GROUP: "拆分活动组",
}
_ACTION_ORDER = {
    Action.PAUSE: 0,
    Action.INCREASE_BUDGET: 1,
    Action.DECREASE_TARGET_ROAS: 1,
    Action.RESUME: 1,
    Action.DECREASE_BUDGET: 2,
    Action.INCREASE_TARGET_ROAS: 2,
    Action.HOLD: 3,
}
_STRUCTURE_ACTIONS = {Action.CREATE_GROUP, Action.SPLIT_GROUP}


@dataclass(frozen=True)
class ShopeeAdsReport:
    store_id: str
    report_date: date
    expected_data_date: date
    actual_data_date: date | None
    generated_at: datetime
    collection: CollectionDay | None
    metrics: tuple[AdMetric, ...]
    recommendations: tuple[Recommendation, ...]
    pending_feedback: tuple[Feedback, ...]


def render_shopee_ads_markdown(report: ShopeeAdsReport) -> str:
    """把已冻结的日报输入确定性渲染为 Markdown。"""
    current = (
        report.actual_data_date == report.expected_data_date
        and report.collection is not None
        and report.collection.status == "success"
    )
    collection_status = _collection_status(report.collection)
    if current:
        report_status = "CURRENT"
    elif report.actual_data_date is None:
        report_status = "NO_DATA"
    elif report.actual_data_date == report.expected_data_date:
        report_status = "STALE / UNVERIFIED"
    elif not current:
        report_status = (
            "STALE / NO_DATA"
            if report.collection is not None and report.collection.status == "no_data"
            else "STALE"
        )
    else:
        report_status = "STALE"

    lines = [
        f"# Shopee 广告优化日报｜{report.report_date.isoformat()}",
        "",
        "> 本报告只提供广告优化分析，所有广告设置均由你在 Shopee 中人工修改。",
        "",
        f"- 状态：{report_status}",
        f"- 采集状态：{collection_status}",
        f"- 预期数据日期：{report.expected_data_date.isoformat()}",
        f"- 实际数据日期：{_date(report.actual_data_date)}",
        f"- 数据日期：{_date(report.actual_data_date)}",
        f"- 生成时间：{_datetime(report.generated_at)}",
        "",
        "## 店铺广告汇总",
        "",
    ]
    lines.extend(_summary_lines(report.metrics))
    lines.extend(("", "## 活动排行", ""))
    lines.extend(_ranking_lines(report.metrics))
    lines.extend(("", "## 今日最新建议", ""))
    if current:
        primary = tuple(
            item
            for item in report.recommendations
            if item.slot is RecommendationSlot.PRIMARY
            and item.action not in _STRUCTURE_ACTIONS
        )
    else:
        primary = ()
    if primary:
        lines.extend(_recommendation_lines(primary, report.metrics))
    else:
        reason = "今日无可用新建议"
        if not current:
            reason += "；旧数据建议不会作为今日建议展示。"
        lines.append(reason)

    lines.extend(("", "## 结构调整待办", ""))
    structures = (
        tuple(
            item
            for item in report.recommendations
            if item.action in _STRUCTURE_ACTIONS
            or item.slot
            in {RecommendationSlot.CREATE_GROUP, RecommendationSlot.SPLIT_GROUP}
        )
        if current
        else ()
    )
    lines.extend(_structure_lines(structures, report.metrics))
    lines.extend(("", "## 已人工执行但待评估", ""))
    lines.extend(_pending_lines(report.pending_feedback))
    lines.extend(("", "## 数据异常与缺失", ""))
    lines.extend(_anomaly_lines(report, current))
    return "\n".join(lines).rstrip() + "\n"


class ShopeeAdsReportService:
    """读取冻结事实并安全写入一个本地日报文件。"""

    def __init__(
        self,
        repository: OptimizerRepository,
        reports_dir: Path = Path("var/reports"),
    ) -> None:
        self.repository = repository
        self.reports_dir = Path(reports_dir)

    def render(self, store_id: str, report_date: date, decision_at: datetime) -> str:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        expected_data_date = report_date - timedelta(days=1)
        snapshot = self.repository.report_snapshot_as_of(
            store_id, expected_data_date, decision_at
        )
        return render_shopee_ads_markdown(
            ShopeeAdsReport(
                store_id=store_id,
                report_date=report_date,
                expected_data_date=expected_data_date,
                actual_data_date=snapshot.actual_data_date,
                generated_at=decision_at,
                collection=snapshot.collection,
                metrics=snapshot.metrics,
                recommendations=snapshot.recommendations,
                pending_feedback=snapshot.pending_feedback,
            )
        )

    def write(self, store_id: str, report_date: date, decision_at: datetime) -> Path:
        content = self.render(store_id, report_date, decision_at)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        target = self.reports_dir / f"shopee-ads-{report_date.isoformat()}.md"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.reports_dir,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
            directory_fd = os.open(self.reports_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target


def _summary_lines(metrics: tuple[AdMetric, ...]) -> list[str]:
    if not metrics:
        return ["没有可汇总的广告数据。"]
    lines: list[str] = []
    for currency in sorted({item.currency for item in metrics}):
        selected = tuple(item for item in metrics if item.currency == currency)
        spend = sum((item.spend for item in selected), Decimal(0))
        gmv = sum((item.gmv for item in selected), Decimal(0))
        orders = sum(item.orders for item in selected)
        roas = None if spend == 0 else gmv / spend
        lines.extend(
            (
                f"- 花费：{_money(spend)} {currency}",
                f"- 广告成交额：{_money(gmv)} {currency}",
                f"- ROAS：{_roas(roas)}",
                f"- 广告归因订单：{orders}",
            )
        )
    return lines


def _ranking_lines(metrics: tuple[AdMetric, ...]) -> list[str]:
    if not metrics:
        return ["没有可排行的活动。"]
    lines = [
        "| 排名 | 活动 | 花费 | 广告成交额 | ROAS | 广告归因订单 |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    ranked = sorted(
        metrics, key=lambda item: (-item.gmv, -item.spend, item.campaign_id)
    )
    for rank, item in enumerate(ranked, start=1):
        lines.append(
            f"| {rank} | {_safe(item.campaign_name)} ({_safe(item.campaign_id)}) | "
            f"{_money(item.spend)} {item.currency} | {_money(item.gmv)} {item.currency} | "
            f"{_roas(item.roas)} | {item.orders} |"
        )
    return lines


def _recommendation_lines(
    recommendations: tuple[Recommendation, ...], metrics: tuple[AdMetric, ...]
) -> list[str]:
    names = {item.campaign_id: item.campaign_name for item in metrics}
    currencies = {item.campaign_id: item.currency for item in metrics}
    ordered = sorted(
        recommendations,
        key=lambda item: (
            _ACTION_ORDER.get(item.action, 3),
            item.priority,
            item.campaign_id,
            item.slot.value,
        ),
    )
    lines: list[str] = []
    for item in ordered:
        name = names.get(item.campaign_id, item.campaign_id)
        currency = currencies.get(item.campaign_id, "")
        lines.extend(_recommendation_detail_lines(item, name, currency))
    return lines


def _structure_lines(
    recommendations: tuple[Recommendation, ...], metrics: tuple[AdMetric, ...]
) -> list[str]:
    if not recommendations:
        return ["暂无结构调整待办。"]
    return _recommendation_lines(recommendations, metrics)


def _recommendation_detail_lines(
    item: Recommendation, campaign_name: str, currency: str
) -> tuple[str, ...]:
    """参数动作与结构动作共用同一份可追溯明细。"""
    return (
        f"### {_safe(campaign_name)} ({_safe(item.campaign_id)})",
        "",
        f"- 动作：{_ACTION_LABELS[item.action]} (`{item.action.value}`)，请人工修改",
        (
            f"- 当前值 | 建议值 | 变动："
            f"{_value(item.current_value, item.action, currency)} | "
            f"{_value(item.proposed_value, item.action, currency)} | "
            f"{_percent(item.change_ratio)}"
        ),
        f"- 置信度：{item.confidence.value}",
        f"- 证据窗口：{_evidence_windows(item)}",
        f"- 依据：{'; '.join(_safe(evidence.summary) for evidence in item.evidence)}",
        f"- 数据质量：{_quality(item)}",
        f"- 观察天数：{item.observation_days}",
        f"- 风险：{_percent(item.risk)}",
        (
            f"- 成功条件：ROAS ≥ "
            f"{_optional_roas(item.success_condition.minimum_roas)}；"
            f"广告成交额增长 ≥ "
            f"{_percent(item.success_condition.minimum_gmv_growth)}；"
            f"观察 {item.success_condition.observation_days} 天"
        ),
        (
            f"- 回退条件：ROAS < "
            f"{_optional_roas(item.rollback_condition.roas_below)} "
            f"连续 {item.rollback_condition.consecutive_days} 天"
        ),
        "",
    )


def _pending_lines(feedback: tuple[Feedback, ...]) -> list[str]:
    if not feedback:
        return ["暂无已人工执行且待评估的动作。"]
    return [
        f"- 建议 {_safe(item.recommendation_id)} 修订 {item.recommendation_revision}；"
        f"执行时间：{_datetime(item.executed_at)}；状态：{item.status.value}；"
        f"实际值：{_decimal(item.actual_value)}；备注：{_safe(item.note) or '无'}。"
        for item in feedback
    ]


def _anomaly_lines(report: ShopeeAdsReport, current: bool) -> list[str]:
    anomalies: list[str] = []
    if report.collection is None:
        anomalies.append("预期数据日没有截止生成时点可见的采集记录。")
    elif report.collection.status != "success":
        anomalies.append(f"预期数据日采集状态为 {_safe(report.collection.status)}。")
    if report.actual_data_date is None:
        anomalies.append("截止生成时点没有可用广告指标。")
    elif not current:
        if report.actual_data_date == report.expected_data_date:
            anomalies.append("同日残留指标仅供参考，未经截止时点成功采集确认。")
        else:
            anomalies.append(
                f"当前仅有 {report.actual_data_date.isoformat()} 数据，晚于该日的建议未生成。"
            )
    for item in report.metrics:
        if item.spend == 0:
            anomalies.append(f"活动 {_safe(item.campaign_name)} 零花费，ROAS 未定义。")
    for recommendation in report.recommendations if current else ():
        for evidence in recommendation.evidence:
            quality = evidence.quality
            if quality.status.value != "healthy" or quality.missing_fields:
                missing = ", ".join(_safe(value) for value in quality.missing_fields)
                anomalies.append(
                    f"活动 {_safe(recommendation.campaign_id)} 数据质量 {quality.status.value}"
                    + (f"，缺失字段：{missing}" if missing else "")
                    + (f"，说明：{_safe(quality.note)}" if quality.note else "")
                    + "。"
                )
    return anomalies or ["未发现影响本次判断的数据异常或缺失。"]


def _collection_status(collection: CollectionDay | None) -> str:
    if collection is None:
        return "无截止时点可见记录"
    return {
        "success": "成功",
        "no_data": "无数据",
        "failed": "失败",
    }.get(collection.status, _safe(collection.status))


def _quality(recommendation: Recommendation) -> str:
    values = []
    for evidence in recommendation.evidence:
        quality = evidence.quality
        value = quality.status.value
        if quality.missing_fields:
            value += (
                "（缺失："
                + ", ".join(_safe(field) for field in quality.missing_fields)
                + "）"
            )
        if quality.note:
            value += f"（{_safe(quality.note)}）"
        values.append(value)
    return "; ".join(dict.fromkeys(values))


def _evidence_windows(recommendation: Recommendation) -> str:
    return "; ".join(
        f"短窗 {item.short_window.start_date.isoformat()}~{item.short_window.end_date.isoformat()}，"
        f"长窗 {item.long_window.start_date.isoformat()}~{item.long_window.end_date.isoformat()}"
        for item in recommendation.evidence
    )


def _safe(value: str) -> str:
    normalized = " ".join(str(value).split())
    normalized = escape(normalized, quote=False).replace("\\", "\\\\")
    for marker in ("*", "[", "]", "(", ")", "_", "#", "`", "|"):
        normalized = normalized.replace(marker, f"\\{marker}")
    return normalized


def _datetime(value: datetime) -> str:
    zone_name = getattr(value.tzinfo, "key", None) or value.tzname() or "UTC"
    return f"{value.strftime('%Y-%m-%d %H:%M:%S')} {zone_name}"


def _date(value: date | None) -> str:
    return "无" if value is None else value.isoformat()


def _decimal(value: Decimal | None) -> str:
    return "—" if value is None else format(value, "f")


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _roas(value: Decimal | None) -> str:
    if value is None:
        return "未定义（零花费）"
    return format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")


def _optional_roas(value: Decimal | None) -> str:
    return "未设置" if value is None else _roas(value)


def _percent(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return (
        format(
            (value * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "f",
        )
        + "%"
    )


def _value(value: Decimal | None, action: Action, currency: str) -> str:
    if value is None:
        return "—"
    if action in {Action.INCREASE_BUDGET, Action.DECREASE_BUDGET}:
        return f"{_money(value)} {currency}".rstrip()
    return _roas(value)

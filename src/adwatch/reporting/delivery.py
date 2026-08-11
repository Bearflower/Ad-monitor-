from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from adwatch.operations.run_state import redact_sensitive_text

# 报告标题模板，支持日报/周报/月报
REPORT_TITLE_TEMPLATES: dict[str, str] = {
    "daily": "广告经营日报 {date}｜{risk_label}",
    "weekly": "广告周报 | {week_start} 至 {week_end}",
    "monthly": "广告月报 | {year}年{month}月",
}


def build_report_title(report_type: str, **kwargs: object) -> str:
    """根据报告类型和参数生成标题。

    Args:
        report_type: 报告类型，可选 'daily'/'weekly'/'monthly'
        **kwargs: 模板参数，如 date/risk_label/week_start/week_end/year/month

    Returns:
        格式化后的标题字符串

    Raises:
        ValueError: 报告类型不支持时抛出
    """
    template = REPORT_TITLE_TEMPLATES.get(report_type)
    if template is None:
        valid = ", ".join(REPORT_TITLE_TEMPLATES)
        raise ValueError(f"未知报告类型 '{report_type}'，支持: {valid}")
    return template.format(**kwargs)


class Transport(Protocol):
    def send(self, url: str, payload: dict[str, object]) -> None: ...


class WebhookTransport:
    def send(self, url: str, payload: dict[str, object]) -> None:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 400:
                raise OSError(f"Feishu returned HTTP {response.status}")


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    path: Path
    error: str | None = None


def deliver_report(
    content: str,
    *,
    data_date: date,
    report_dir: Path,
    webhook_url: str,
    transport: Transport | None = None,
    header_template: str = "green",
    risk_label: str = "正常",
    title: str | None = None,
    filename: str | None = None,
    completeness: str | None = None,
) -> DeliveryResult:
    """交付报告到飞书 webhook 并保存到本地。

    Args:
        content: Markdown 报告内容
        data_date: 报告数据日期
        report_dir: 报告保存目录
        webhook_url: 飞书 webhook 地址
        transport: 自定义传输层（用于测试注入）
        header_template: 飞书卡片 header 颜色模板
        risk_label: 风险标签（如"正常"/"高风险"）
        title: 飞书消息标题，默认为日报模板
        filename: 本地文件名，默认为 daily-{data_date}.md
        completeness: 完整度指示，如"缺1天"，非空时追加到标题

    Returns:
        DeliveryResult 交付结果
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    local_filename = filename or f"daily-{data_date.isoformat()}.md"
    path = report_dir / local_filename

    # 构建标题：优先使用传入 title，否则使用日报模板
    card_title = title or build_report_title(
        "daily", date=data_date.isoformat(), risk_label=risk_label,
    )
    if completeness:
        card_title = f"{card_title}（{completeness}）"

    payload: dict[str, object] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": header_template,
                "title": {
                    "tag": "plain_text",
                    "content": card_title,
                }
            },
            "elements": [{"tag": "markdown", "content": content}],
        },
    }
    error_message = None
    if webhook_url:
        sender = transport or WebhookTransport()
        for _ in range(3):
            try:
                sender.send(webhook_url, payload)
                _write_atomic(path, content)
                return DeliveryResult(status="sent", path=path)
            except OSError as error:
                error_message = f"{type(error).__name__}: {error}"
    else:
        error_message = "Feishu webhook is not configured"

    _write_atomic(path, content)
    return DeliveryResult(status="fallback", path=path, error=error_message)


def deliver_incident(
    *,
    stage: str,
    report_date: date,
    message: str,
    log_path: Path,
    webhook_url: str,
    transport: Transport | None = None,
) -> DeliveryResult:
    safe_message = redact_sensitive_text(message)
    payload: dict[str, object] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": "red",
                "title": {
                    "tag": "plain_text",
                    "content": (
                        f"Adwatch 自动任务故障｜{report_date.isoformat()}"
                    ),
                },
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**阶段**：{stage}\n"
                        f"**日报日期**：{report_date.isoformat()}\n"
                        f"**故障**：{safe_message}\n"
                        f"**本地日志**：{log_path}"
                    ),
                }
            ],
        },
    }
    error_message = None
    if webhook_url:
        sender = transport or WebhookTransport()
        for _ in range(3):
            try:
                sender.send(webhook_url, payload)
                return DeliveryResult(status="sent", path=log_path)
            except OSError as error:
                error_message = f"{type(error).__name__}: {error}"
    else:
        error_message = "Feishu webhook is not configured"
    return DeliveryResult(
        status="fallback",
        path=log_path,
        error=error_message,
    )


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)

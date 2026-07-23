from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol


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
) -> DeliveryResult:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"daily-{data_date.isoformat()}.md"
    payload: dict[str, object] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"广告每日快报 {data_date.isoformat()}",
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


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)

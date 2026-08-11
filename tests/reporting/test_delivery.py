from datetime import date

from adwatch.reporting.delivery import deliver_incident, deliver_report


class FailingTransport:
    def __init__(self):
        self.calls = 0

    def send(self, url, payload):
        self.calls += 1
        raise OSError("network unavailable")


class CapturingTransport:
    def __init__(self):
        self.payload = None

    def send(self, url, payload):
        self.payload = payload


def test_delivery_failure_writes_local_fallback(tmp_path):
    transport = FailingTransport()
    result = deliver_report(
        "# Daily",
        data_date=date(2026, 7, 22),
        report_dir=tmp_path,
        webhook_url="https://example.invalid/secret",
        transport=transport,
    )
    assert result.status == "fallback"
    assert result.path.read_text() == "# Daily"
    assert transport.calls == 3


def test_delivery_uses_risk_label_and_header_color(tmp_path):
    transport = CapturingTransport()

    result = deliver_report(
        "# 经营日报",
        data_date=date(2026, 7, 27),
        report_dir=tmp_path,
        webhook_url="https://example.test/hook",
        transport=transport,
        header_template="red",
        risk_label="高风险",
    )

    assert result.status == "sent"
    assert transport.payload["card"]["header"]["template"] == "red"
    title = transport.payload["card"]["header"]["title"]["content"]
    assert "高风险" in title


def test_deliver_incident_sends_red_card_without_secrets(tmp_path):
    transport = CapturingTransport()
    log_path = tmp_path / "adwatch-faults.jsonl"

    result = deliver_incident(
        stage="bridge",
        report_date=date(2026, 7, 29),
        message="Bridge unavailable",
        log_path=log_path,
        webhook_url="https://example.test/hook/webhook-secret",
        transport=transport,
    )

    assert result.status == "sent"
    assert result.path == log_path
    assert transport.payload["card"]["header"]["template"] == "red"
    payload = str(transport.payload)
    assert "Bridge unavailable" in payload
    assert "https://example.test/hook/webhook-secret" not in payload


def test_deliver_incident_retries_without_creating_daily_report(tmp_path):
    transport = FailingTransport()
    log_path = tmp_path / "logs" / "adwatch-faults.jsonl"

    result = deliver_incident(
        stage="collection",
        report_date=date(2026, 7, 29),
        message="采集失败",
        log_path=log_path,
        webhook_url="https://example.invalid/hook/secret",
        transport=transport,
    )

    assert result.status == "fallback"
    assert transport.calls == 3
    assert not list(tmp_path.rglob("daily-*.md"))

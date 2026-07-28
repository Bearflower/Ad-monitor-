from datetime import date

from adwatch.reporting.delivery import deliver_report


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

from datetime import date

from adwatch.reporting.delivery import deliver_report


class FailingTransport:
    def __init__(self):
        self.calls = 0

    def send(self, url, payload):
        self.calls += 1
        raise OSError("network unavailable")


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

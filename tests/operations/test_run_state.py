import json

import pytest

from adwatch.operations.run_state import (
    Incident,
    RunLockUnavailable,
    RunStateStore,
)


def _incident(message: str = "无法连接紫鸟 Bridge") -> Incident:
    return Incident(
        run_id="run-1",
        occurred_at="2026-07-30T09:01:00+08:00",
        execution_date="2026-07-30",
        report_date="2026-07-29",
        stage="bridge",
        error_type="BridgeUnavailable",
        message=message,
        exit_code=2,
    )


def test_incident_is_logged_and_notification_is_deduplicated(tmp_path):
    store = RunStateStore(tmp_path)
    incident = _incident()

    store.record_incident(incident)

    assert store.should_notify(incident) is True
    store.mark_notified(incident)
    assert store.should_notify(incident) is False
    logged = json.loads(store.fault_log.read_text().splitlines()[0])
    assert logged["stage"] == "bridge"
    assert logged["dedupe_key"] == incident.dedupe_key
    assert {
        "retryable",
        "retry_count",
        "report_path",
        "notification_status",
    } <= logged.keys()


def test_second_nonblocking_run_lock_fails(tmp_path):
    store = RunStateStore(tmp_path)

    with (
        store.lock(),
        pytest.raises(RunLockUnavailable),
        store.lock(),
    ):
        pass


def test_state_is_replaced_atomically_without_temporary_file(tmp_path):
    store = RunStateStore(tmp_path)

    store.write_state({"last_successful_report_date": "2026-07-28"})
    store.write_state({"last_successful_report_date": "2026-07-29"})

    assert store.read_state() == {
        "last_successful_report_date": "2026-07-29"
    }
    assert not store.state_path.with_suffix(".json.tmp").exists()


def test_incident_log_redacts_webhook_query_and_environment_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADWATCH_TEST_SECRET", "top-secret-value")
    message = (
        "request failed at "
        "https://open.feishu.cn/open-apis/bot/v2/hook/webhook-token"
        "?signature=query-secret "
        "credential=top-secret-value"
    )
    store = RunStateStore(tmp_path)

    store.record_incident(_incident(message))

    contents = store.fault_log.read_text()
    assert "webhook-token" not in contents
    assert "query-secret" not in contents
    assert "top-secret-value" not in contents
    assert "[REDACTED]" in contents

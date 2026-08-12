from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import adwatch.operations.scheduled_daily as scheduled_daily_module
from adwatch.config import Settings
from adwatch.operations.daily_run import DailyRunResult
from adwatch.operations.run_state import RunStateStore
from adwatch.operations.scheduled_daily import (
    decide_scheduled_run,
    run_scheduled_daily,
)


class FakeDailyService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _result(status="ok", exit_code=0, metrics_processed=0):
    return DailyRunResult(
        exit_code=exit_code,
        status=status,
        report_path=None,
        collection_errors=(),
        metrics_processed=metrics_processed,
    )


def test_login_after_nine_runs_missing_report():
    decision = decide_scheduled_run(
        now=datetime(
            2026,
            7,
            30,
            9,
            5,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        run_time=time(9, 0),
        last_successful_report_date=None,
    )

    assert decision.should_run is True
    assert decision.report_date == date(2026, 7, 29)


def test_login_before_nine_and_already_successful_report_are_noops():
    before = decide_scheduled_run(
        now=datetime(
            2026,
            7,
            30,
            8,
            59,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        run_time=time(9, 0),
        last_successful_report_date=None,
    )
    completed = decide_scheduled_run(
        now=datetime(
            2026,
            7,
            30,
            9,
            5,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        run_time=time(9, 0),
        last_successful_report_date=date(2026, 7, 29),
    )

    assert before.should_run is False
    assert completed.should_run is False


def test_scheduled_run_records_success_and_is_idempotent(tmp_path):
    settings = Settings(data_dir=tmp_path)
    service = FakeDailyService(_result())
    now = datetime(
        2026,
        7,
        30,
        9,
        5,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )

    first = run_scheduled_daily(
        settings,
        now=now,
        daily_service=service,
    )
    second = run_scheduled_daily(
        settings,
        now=now,
        daily_service=service,
    )

    assert first == 0
    assert second == 0
    assert len(service.calls) == 1
    assert RunStateStore(tmp_path).last_successful_report_date() == date(
        2026, 7, 29
    )


def test_partial_run_does_not_mark_report_successful(tmp_path):
    settings = Settings(data_dir=tmp_path)
    service = FakeDailyService(_result(status="partial", exit_code=2))

    result = run_scheduled_daily(
        settings,
        now=datetime(
            2026,
            7,
            30,
            9,
            5,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        daily_service=service,
    )

    assert result == 2
    assert RunStateStore(tmp_path).last_successful_report_date() is None


def test_scheduled_run_publishes_collected_facts(tmp_path, monkeypatch, capsys):
    settings = Settings(
        data_dir=tmp_path,
        publish_ssh_target="root@43.156.242.184",
    )
    service = FakeDailyService(_result(metrics_processed=1))
    calls = []
    monkeypatch.setattr(
        scheduled_daily_module,
        "_publish_scheduled_facts",
        lambda selected, report_date: calls.append((selected, report_date))
        or "replicate_import=ok rows=4",
    )

    result = run_scheduled_daily(
        settings,
        now=datetime(
            2026, 7, 30, 9, 5, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        daily_service=service,
    )

    assert result == 0
    assert calls == [(settings, date(2026, 7, 29))]
    assert "scheduled_publish=ok" in capsys.readouterr().out


def test_publish_failure_keeps_scheduled_run_retryable(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        publish_ssh_target="root@43.156.242.184",
    )
    service = FakeDailyService(_result(metrics_processed=1))

    def fail(*args):
        raise RuntimeError("remote unavailable")

    monkeypatch.setattr(
        scheduled_daily_module, "_publish_scheduled_facts", fail
    )

    result = run_scheduled_daily(
        settings,
        now=datetime(
            2026, 7, 30, 9, 5, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        daily_service=service,
    )

    assert result == 2
    assert RunStateStore(tmp_path).last_successful_report_date() is None

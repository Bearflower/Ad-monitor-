from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from adwatch.optimizer.daily import (
    DailyRunResult,
    FileRunLock,
    OptimizerDailyService,
    RunLocked,
)
from adwatch.optimizer.models import DataQuality, DataQualityStatus
from adwatch.optimizer.repository import RunLog

NOW = datetime(2026, 9, 11, 1, tzinfo=UTC)
REPORT_DATE = date(2026, 9, 11)
DATA_DATE = date(2026, 9, 10)


class _Repository:
    def __init__(self) -> None:
        self.logs: dict[str, RunLog] = {}

    def save_run_log(self, log: RunLog) -> RunLog:
        self.logs[log.run_id] = log
        return log

    def get_run_log(self, run_id: str) -> RunLog | None:
        return self.logs.get(run_id)


class _Stage:
    def __init__(self, events: list[str], name: str, result: object = None) -> None:
        self.events = events
        self.name = name
        self.result = result

    def migrate(self) -> None:
        self.events.append(self.name)

    def ensure_ready(self) -> object:
        self.events.append(self.name)
        return self.result

    def run(self, store_id: str, as_of: date) -> object:
        assert store_id == "store-1"
        assert as_of == REPORT_DATE
        self.events.append(self.name)
        return self.result

    def collect(self, data_date: date) -> object:
        assert data_date == DATA_DATE
        self.events.append(self.name)
        return self.result

    def ensure_profiles(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> object:
        assert (store_id, data_date, decision_at) == ("store-1", DATA_DATE, NOW)
        self.events.append(self.name)
        return self.result

    def assess(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> DataQuality:
        assert (store_id, data_date, decision_at) == ("store-1", DATA_DATE, NOW)
        self.events.append(self.name)
        return DataQuality(DataQualityStatus.HEALTHY, NOW, True)

    def evaluate_pending(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> object:
        assert (store_id, data_date, decision_at) == ("store-1", DATA_DATE, NOW)
        self.events.append(self.name)
        return self.result

    def recommend(
        self,
        store_id: str,
        data_date: date,
        quality: DataQuality,
        decision_at: datetime,
    ) -> object:
        assert (store_id, data_date, decision_at) == ("store-1", DATA_DATE, NOW)
        assert quality.status is DataQualityStatus.HEALTHY
        self.events.append(self.name)
        return self.result

    def write(self, store_id: str, report_date: date, decision_at: datetime) -> Path:
        assert (store_id, report_date, decision_at) == ("store-1", REPORT_DATE, NOW)
        self.events.append(self.name)
        return Path("report.md")


class _PartialBackfill(_Stage):
    class Result:
        complete = False
        failed_dates = (DATA_DATE,)

    def __init__(self, events: list[str]) -> None:
        super().__init__(events, "backfill", self.Result())


def _service(events: list[str], **changes: object) -> OptimizerDailyService:
    repository = changes.pop("repository", _Repository())

    @contextmanager
    def lock():
        events.append("lock")
        yield

    dependencies = {
        "repository": repository,
        "migrator": _Stage(events, "migrate"),
        "session": _Stage(events, "session"),
        "backfill": _Stage(events, "backfill", object()),
        "collector": _Stage(events, "collect", object()),
        "profiles": _Stage(events, "profiles"),
        "quality": _Stage(events, "quality"),
        "evaluator": _Stage(events, "evaluate"),
        "optimizer": _Stage(events, "recommend"),
        "reporter": _Stage(events, "report"),
        "run_lock": lock,
        "clock": lambda: NOW,
    }
    dependencies.update(changes)
    return OptimizerDailyService("store-1", **dependencies)


def test_initialize_runs_migration_through_report_in_required_order() -> None:
    events: list[str] = []
    repository = _Repository()
    service = _service(events, repository=repository)

    result = service.initialize(REPORT_DATE, decision_at=NOW)
    repeated = service.initialize(REPORT_DATE, decision_at=NOW)

    assert result == DailyRunResult("success", REPORT_DATE, Path("report.md"))
    assert repeated == DailyRunResult(
        "success", REPORT_DATE, Path("report.md"), True
    )
    assert events == [
        "lock", "migrate", "session", "backfill", "profiles", "quality",
        "evaluate", "recommend", "report", "lock", "migrate",
    ]


def test_daily_locks_then_runs_stages_and_success_is_idempotent() -> None:
    events: list[str] = []
    repository = _Repository()
    service = _service(events, repository=repository)

    first = service.run_daily(REPORT_DATE, decision_at=NOW)
    second = service.run_daily(REPORT_DATE, decision_at=NOW)

    assert first.status == "success"
    assert second == DailyRunResult("success", REPORT_DATE, Path("report.md"), True)
    assert events == [
        "lock", "session", "backfill", "quality", "evaluate", "recommend",
        "report", "lock",
    ]
    assert repository.logs["daily:store-1:2026-09-11"].status == "success"
    assert {
        log.details["stage"]
        for log in repository.logs.values()
        if log.details and "stage" in log.details
    } == {"session", "backfill", "quality", "evaluate", "recommend", "report"}


def test_daily_analysis_cutoff_is_refreshed_after_backfill() -> None:
    events: list[str] = []
    current_time = [NOW]
    after_backfill = NOW + timedelta(minutes=1)
    analysis_times: list[datetime] = []

    class AdvancingBackfill(_Stage):
        def run(self, store_id: str, as_of: date) -> object:
            assert (store_id, as_of) == ("store-1", REPORT_DATE)
            self.events.append(self.name)
            current_time[0] = after_backfill
            return object()

    class AnalysisStages:
        def assess(
            self, store_id: str, data_date: date, decision_at: datetime
        ) -> DataQuality:
            assert (store_id, data_date) == ("store-1", DATA_DATE)
            analysis_times.append(decision_at)
            events.append("quality")
            return DataQuality(DataQualityStatus.HEALTHY, decision_at, True)

        def evaluate_pending(
            self, store_id: str, data_date: date, decision_at: datetime
        ) -> None:
            assert (store_id, data_date) == ("store-1", DATA_DATE)
            analysis_times.append(decision_at)
            events.append("evaluate")

        def recommend(
            self,
            store_id: str,
            data_date: date,
            quality: DataQuality,
            decision_at: datetime,
        ) -> None:
            assert (store_id, data_date) == ("store-1", DATA_DATE)
            assert quality.status is DataQualityStatus.HEALTHY
            analysis_times.append(decision_at)
            events.append("recommend")

        def write(
            self, store_id: str, report_date: date, decision_at: datetime
        ) -> Path:
            assert (store_id, report_date) == ("store-1", REPORT_DATE)
            analysis_times.append(decision_at)
            events.append("report")
            return Path("report.md")

    analysis = AnalysisStages()
    service = _service(
        events,
        backfill=AdvancingBackfill(events, "backfill"),
        quality=analysis,
        evaluator=analysis,
        optimizer=analysis,
        reporter=analysis,
        clock=lambda: current_time[0],
    )

    result = service.run_daily(REPORT_DATE)

    assert result.status == "success"
    assert analysis_times == [after_backfill] * 4


def test_collection_failure_stops_before_analysis_and_logs_safe_error() -> None:
    events: list[str] = []
    repository = _Repository()
    service = _service(
        events, repository=repository, backfill=_PartialBackfill(events)
    )

    result = service.run_daily(REPORT_DATE, decision_at=NOW)

    assert result.status == "failed"
    assert events == ["lock", "session", "backfill"]
    failed = repository.logs["daily:store-1:2026-09-11"]
    assert failed.details == {"stage": "backfill", "error_type": "collection_incomplete"}
    assert repository.logs["daily:store-1:2026-09-11:backfill"].status == "failed"
    assert "secret" not in repr(repository.logs)


def test_analysis_exception_never_writes_report_or_exception_message() -> None:
    events: list[str] = []
    repository = _Repository()

    class BrokenOptimizer(_Stage):
        def recommend(self, *args: object, **kwargs: object) -> object:
            self.events.append(self.name)
            raise RuntimeError("secret browser token")

    service = _service(
        events,
        repository=repository,
        optimizer=BrokenOptimizer(events, "recommend"),
    )

    result = service.run_daily(REPORT_DATE, decision_at=NOW)

    assert result.status == "failed"
    assert "report" not in events
    assert repository.logs["daily:store-1:2026-09-11"].details == {
        "stage": "recommend",
        "error_type": "stage_failure",
    }
    assert "secret browser token" not in repr(repository.logs)


def test_lock_contention_does_not_start_session() -> None:
    events: list[str] = []

    @contextmanager
    def locked():
        raise RunLocked("busy")
        yield

    result = _service(events, run_lock=locked).run_daily(
        REPORT_DATE, decision_at=NOW
    )

    assert result.status == "locked"
    assert events == []


def test_daily_requires_aware_decision_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _service([]).run_daily(REPORT_DATE, decision_at=NOW.replace(tzinfo=None))


def test_collect_day_is_locked_shopee_only_and_structurally_logged() -> None:
    events: list[str] = []
    repository = _Repository()

    result = _service(events, repository=repository).collect_day(
        DATA_DATE, decision_at=NOW
    )

    assert result == "success"
    assert events == ["lock", "session", "collect"]
    assert repository.logs["collect:store-1:2026-09-10"].status == "success"


def test_run_log_persistence_failure_stops_pipeline_without_escaping() -> None:
    events: list[str] = []

    class BrokenRepository(_Repository):
        def save_run_log(self, log: RunLog) -> RunLog:
            raise OSError("secret database path")

    result = _service(events, repository=BrokenRepository()).run_daily(
        REPORT_DATE, decision_at=NOW
    )

    assert result.status == "failed"
    assert events == ["lock", "session"]


def test_collect_final_log_failure_is_safe_and_does_not_escape() -> None:
    events: list[str] = []

    class BrokenFinalLogRepository(_Repository):
        def save_run_log(self, log: RunLog) -> RunLog:
            if log.run_id == "collect:store-1:2026-09-10":
                raise OSError("secret database path")
            return super().save_run_log(log)

    result = _service(
        events, repository=BrokenFinalLogRepository()
    ).collect_day(DATA_DATE, decision_at=NOW)

    assert result == "failed"
    assert events == ["lock", "session", "collect"]


def test_file_run_lock_rejects_a_second_holder(tmp_path: Path) -> None:
    lock = FileRunLock(tmp_path / "optimizer.lock")

    with lock(), pytest.raises(RunLocked), lock():
        pytest.fail("second holder must not enter")

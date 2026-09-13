"""Shopee 广告优化初始化与每日只读分析编排。"""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from adwatch.optimizer.models import DataQuality
from adwatch.optimizer.repository import OptimizerRepository, RunLog


class RunLocked(RuntimeError):
    """同一优化器已有一次运行持有本机互斥锁。"""


class Migrator(Protocol):
    def migrate(self) -> None: ...


class SessionStage(Protocol):
    def ensure_ready(self) -> object: ...


class BackfillStage(Protocol):
    def run(self, store_id: str, as_of: date) -> object: ...


class CollectionStage(Protocol):
    def collect(self, data_date: date) -> object: ...


class ProfileStage(Protocol):
    def ensure_profiles(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> object: ...


class QualityStage(Protocol):
    def assess(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> DataQuality: ...


class EvaluationStage(Protocol):
    def evaluate_pending(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> object: ...


class RecommendationStage(Protocol):
    def recommend(
        self,
        store_id: str,
        data_date: date,
        quality: DataQuality,
        decision_at: datetime,
    ) -> object: ...


class ReportStage(Protocol):
    def write(self, store_id: str, report_date: date, decision_at: datetime) -> Path: ...


RunLock = Callable[[], object]


@dataclass(frozen=True)
class DailyRunResult:
    status: str
    report_date: date
    report_path: Path | None = None
    skipped: bool = False


class FileRunLock:
    """以非阻塞 advisory lock 保证本机只有一个优化器运行。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def __call__(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RunLocked("optimizer run is already active") from error
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class OptimizerDailyService:
    """按固定顺序执行阶段，并只记录安全、结构化的运行结果。"""

    def __init__(
        self,
        store_id: str,
        *,
        repository: OptimizerRepository,
        migrator: Migrator,
        session: SessionStage,
        backfill: BackfillStage,
        collector: CollectionStage,
        profiles: ProfileStage,
        quality: QualityStage,
        evaluator: EvaluationStage,
        optimizer: RecommendationStage,
        reporter: ReportStage,
        run_lock: RunLock,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not store_id.strip():
            raise ValueError("store_id must not be empty")
        self.store_id = store_id
        self.repository = repository
        self.migrator = migrator
        self.session = session
        self.backfill = backfill
        self.collector = collector
        self.profiles = profiles
        self.quality = quality
        self.evaluator = evaluator
        self.optimizer = optimizer
        self.reporter = reporter
        self.run_lock = run_lock
        self.clock = clock

    def initialize(
        self, report_date: date, *, decision_at: datetime | None = None
    ) -> DailyRunResult:
        now = self._decision_at(decision_at)
        run_prefix = self._run_id("init", report_date)
        try:
            with self.run_lock():  # type: ignore[attr-defined]
                try:
                    self._stage(run_prefix, "migrate", now, self.migrator.migrate)
                except _StageFailed as error:
                    return self._failed_result(run_prefix, report_date, now, error)
                previous = self._existing_success(run_prefix)
                if previous is not None:
                    return DailyRunResult(
                        "success", report_date, _report_path(previous), True
                    )
                try:
                    return self._analysis_pipeline(
                        run_prefix, report_date, now, include_profiles=True
                    )
                except _StageFailed as error:
                    return self._failed_result(run_prefix, report_date, now, error)
        except RunLocked:
            return DailyRunResult("locked", report_date)

    def run_daily(
        self, report_date: date, *, decision_at: datetime | None = None
    ) -> DailyRunResult:
        now = self._decision_at(decision_at)
        run_id = self._run_id("daily", report_date)
        try:
            with self.run_lock():  # type: ignore[attr-defined]
                previous = self.repository.get_run_log(run_id)
                if previous is not None and previous.status == "success":
                    path = _report_path(previous)
                    return DailyRunResult("success", report_date, path, True)
                try:
                    return self._analysis_pipeline(
                        run_id, report_date, now, include_profiles=False
                    )
                except _StageFailed as error:
                    return self._failed_result(run_id, report_date, now, error)
        except RunLocked:
            return DailyRunResult("locked", report_date)

    def collect_day(
        self, data_date: date, *, decision_at: datetime | None = None
    ) -> str:
        now = self._decision_at(decision_at)
        run_id = self._run_id("collect", data_date)
        try:
            with self.run_lock():  # type: ignore[attr-defined]
                self._stage(run_id, "session", now, self.session.ensure_ready)
                result = self._stage(
                    run_id,
                    "collect",
                    now,
                    lambda: _require_complete(self.collector.collect(data_date)),
                )
                status = (
                    "no_data"
                    if hasattr(result, "metrics") and not result.metrics  # type: ignore[attr-defined]
                    else "success"
                )
                return (
                    status
                    if self._save_run_log(
                        RunLog(
                            run_id,
                            status,
                            now,
                            self.clock().astimezone(UTC),
                            {},
                        )
                    )
                    else "failed"
                )
        except RunLocked:
            return "locked"
        except _StageFailed as error:
            self._failed_result(run_id, data_date, now, error)
            return "failed"

    def _analysis_pipeline(
        self,
        run_id: str,
        report_date: date,
        decision_at: datetime,
        *,
        include_profiles: bool,
    ) -> DailyRunResult:
        data_date = report_date - timedelta(days=1)
        self._stage(run_id, "session", decision_at, self.session.ensure_ready)
        self._stage(
            run_id,
            "backfill",
            decision_at,
            lambda: _require_complete(
                self.backfill.run(self.store_id, report_date)
            ),
        )
        if include_profiles:
            self._stage(
                run_id,
                "profiles",
                decision_at,
                lambda: self.profiles.ensure_profiles(
                    self.store_id, data_date, decision_at
                ),
            )
        quality = self._stage(
            run_id,
            "quality",
            decision_at,
            lambda: self.quality.assess(self.store_id, data_date, decision_at),
        )
        assert isinstance(quality, DataQuality)
        self._stage(
            run_id,
            "evaluate",
            decision_at,
            lambda: self.evaluator.evaluate_pending(
                self.store_id, data_date, decision_at
            ),
        )
        self._stage(
            run_id,
            "recommend",
            decision_at,
            lambda: self.optimizer.recommend(
                self.store_id, data_date, quality, decision_at
            ),
        )
        report_path = self._stage(
            run_id,
            "report",
            decision_at,
            lambda: self.reporter.write(self.store_id, report_date, decision_at),
        )
        assert isinstance(report_path, Path)
        finished = self.clock().astimezone(UTC)
        if not self._save_run_log(
            RunLog(
                run_id,
                "success",
                decision_at,
                finished,
                {"report_path": str(report_path)},
            )
        ):
            return DailyRunResult("failed", report_date)
        return DailyRunResult("success", report_date, report_path)

    def _stage(
        self,
        run_id: str,
        stage: str,
        started_at: datetime,
        operation: Callable[[], object],
    ) -> object:
        stage_id = f"{run_id}:{stage}"
        try:
            result = operation()
        except Exception as error:
            error_type = _safe_error_type(error)
            self._save_run_log(
                RunLog(
                    stage_id,
                    "failed",
                    started_at,
                    self.clock().astimezone(UTC),
                    {"stage": stage, "error_type": error_type},
                )
            )
            raise _StageFailed(stage, error_type) from error
        if not self._save_run_log(
            RunLog(
                stage_id,
                "success",
                started_at,
                self.clock().astimezone(UTC),
                {"stage": stage},
            )
        ):
            raise _StageFailed(stage, "persistence_failure")
        return result

    def _failed_result(
        self,
        run_id: str,
        report_date: date,
        started_at: datetime,
        error: _StageFailed,
    ) -> DailyRunResult:
        self._save_run_log(
            RunLog(
                run_id,
                "failed",
                started_at,
                self.clock().astimezone(UTC),
                {"stage": error.stage, "error_type": error.error_type},
            )
        )
        return DailyRunResult("failed", report_date)

    def _save_run_log(self, log: RunLog) -> bool:
        try:
            self.repository.save_run_log(log)
        except Exception:  # noqa: BLE001 - 日志故障不能泄漏底层异常
            return False
        return True

    def _existing_success(self, run_id: str) -> RunLog | None:
        try:
            existing = self.repository.get_run_log(run_id)
        except Exception:  # noqa: BLE001 - 首次迁移前运行日志表可能不存在
            return None
        return existing if existing is not None and existing.status == "success" else None

    def _run_id(self, kind: str, report_date: date) -> str:
        return f"{kind}:{self.store_id}:{report_date.isoformat()}"

    def _decision_at(self, value: datetime | None) -> datetime:
        result = self.clock() if value is None else value
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        return result.astimezone(UTC)


class _StageFailed(RuntimeError):
    def __init__(self, stage: str, error_type: str) -> None:
        super().__init__(stage)
        self.stage = stage
        self.error_type = error_type


class _CollectionIncomplete(RuntimeError):
    pass


def _safe_error_type(error: Exception) -> str:
    if isinstance(error, _CollectionIncomplete):
        return "collection_incomplete"
    if isinstance(error, (TypeError, ValueError)):
        return "contract_error"
    if isinstance(error, OSError):
        return "runtime_unavailable"
    return "stage_failure"


def _require_complete(result: object) -> object:
    if getattr(result, "complete", True) is False:
        raise _CollectionIncomplete
    return result


def _report_path(log: RunLog) -> Path | None:
    raw = None if log.details is None else log.details.get("report_path")
    return Path(raw) if isinstance(raw, str) and raw else None

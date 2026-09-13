"""Shopee Ads 历史业务日回填与可恢复的逐日编排。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from adwatch.config import Settings
from adwatch.optimizer.repository import ClaimLost, OptimizerRepository


class BackfillCollector(Protocol):
    def collect(self, data_date: date) -> object: ...


@dataclass(frozen=True)
class BackfillError:
    """回填失败的安全分类，不保存异常原文。"""

    data_date: date
    error_type: str


@dataclass(frozen=True)
class BackfillResult:
    """一次回填运行的不可变摘要。"""

    requested_dates: tuple[date, ...]
    successful_dates: tuple[date, ...]
    no_data_dates: tuple[date, ...]
    failed_dates: tuple[date, ...]
    errors: tuple[BackfillError, ...] = ()
    metrics_count: int = 0
    snapshots_count: int = 0

    @property
    def complete(self) -> bool:
        return not self.failed_dates

    @property
    def partial(self) -> bool:
        return bool(self.failed_dates)

    @property
    def status(self) -> str:
        return "partial" if self.partial else "complete"

    @property
    def metrics(self) -> int:
        """本次成功回填写入的指标条数。"""
        return self.metrics_count

    @property
    def snapshots(self) -> int:
        """本次成功回填写入的快照条数。"""
        return self.snapshots_count

    @property
    def diagnostics(self) -> tuple[BackfillError, ...]:
        """按日期归档的安全错误诊断。"""
        return self.errors


class BackfillService:
    """按日期顺序回填缺失、失败及到期 no_data 的业务日。"""

    def __init__(
        self,
        repository: OptimizerRepository,
        collector: BackfillCollector,
        history_days: int | None = None,
        no_data_recheck_days: int | None = None,
        timezone: str | ZoneInfo | None = None,
        claim_minutes: int | None = None,
    ) -> None:
        settings = getattr(collector, "settings", None) or Settings(data_dir=Path("."))
        self.repository = repository
        self.collector = collector
        self.history_days = (
            getattr(settings, "history_days", 30)
            if history_days is None
            else history_days
        )
        self.no_data_recheck_days = (
            settings.no_data_recheck_days
            if no_data_recheck_days is None
            else no_data_recheck_days
        )
        self.claim_minutes = (
            settings.backfill_claim_minutes if claim_minutes is None else claim_minutes
        )
        raw_timezone = timezone or settings.timezone
        self.timezone = (
            raw_timezone
            if isinstance(raw_timezone, ZoneInfo)
            else ZoneInfo(raw_timezone)
        )
        _require_positive_int(self.history_days, "history_days")
        _require_positive_int(self.no_data_recheck_days, "no_data_recheck_days")
        _require_positive_int(self.claim_minutes, "claim_minutes")

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        repository: OptimizerRepository,
        collector: BackfillCollector,
    ) -> BackfillService:
        return cls(
            repository,
            collector,
            settings.history_days,
            settings.no_data_recheck_days,
            settings.timezone,
            settings.backfill_claim_minutes,
        )

    def run(self, store_id: str, as_of: date) -> BackfillResult:
        if isinstance(as_of, datetime) or not isinstance(as_of, date):
            raise TypeError("as_of must be a date")
        if not isinstance(store_id, str) or not store_id.strip():
            raise ValueError("store_id must not be empty")

        start = as_of - timedelta(days=self.history_days)
        end = as_of - timedelta(days=1)
        existing = {
            item.data_date: item
            for item in self.repository.list_collection_days(store_id, start, end)
        }
        candidates = tuple(
            current
            for current in _date_range(start, end)
            if _should_request(
                existing.get(current), as_of, self.no_data_recheck_days, self.timezone
            )
        )

        successful: list[date] = []
        no_data: list[date] = []
        failed: list[date] = []
        errors: list[BackfillError] = []
        metrics_count = 0
        snapshots_count = 0
        requested_dates: list[date] = []
        for data_date in candidates:
            attempted_at = datetime.now(UTC).astimezone(self.timezone)
            claim_token = uuid4().hex
            try:
                claimed = self.repository.claim_collection_day(
                    store_id,
                    data_date,
                    claim_token,
                    attempted_at + timedelta(minutes=self.claim_minutes),
                )
            except Exception:  # noqa: BLE001 - 单日领取失败不能阻塞后续日期
                requested_dates.append(data_date)
                failed.append(data_date)
                errors.append(BackfillError(data_date, "persistence_failure"))
                continue
            if not claimed:
                continue
            requested_dates.append(data_date)
            try:
                result = self.collector.collect(data_date, claim_token=claim_token)
                try:
                    complete = result.complete  # type: ignore[attr-defined]
                    metrics = _dedupe_metrics(result.metrics)  # type: ignore[attr-defined]
                    snapshots = _dedupe_snapshots(result.snapshots)  # type: ignore[attr-defined]
                    persisted = getattr(result, "persisted", False)
                except (AttributeError, TypeError):
                    raise TypeError("collector result contract is invalid")
                if not isinstance(complete, bool):
                    raise TypeError("collector result contract is invalid")
                if not complete:
                    failed.append(data_date)
                    errors.append(BackfillError(data_date, "incomplete_collection"))
                    self._safe_record_failure(
                        store_id,
                        data_date,
                        "incomplete_collection",
                        attempted_at,
                        errors,
                        claim_token,
                    )
                elif metrics:
                    if not persisted:
                        self.repository.replace_store_day(
                            store_id,
                            data_date,
                            metrics,
                            snapshots,
                            "success",
                            note="backfill",
                            claim_token=claim_token,
                        )
                    successful.append(data_date)
                    metrics_count += len(metrics)
                    snapshots_count += len(snapshots)
                else:
                    next_retry_at = attempted_at + timedelta(
                        days=self.no_data_recheck_days
                    )
                    if not persisted:
                        self.repository.record_collection_day(
                            store_id,
                            data_date,
                            "no_data",
                            note="backfill:no_data",
                            attempted_at=attempted_at,
                            next_retry_at=next_retry_at,
                            claim_token=claim_token,
                        )
                    no_data.append(data_date)
            except Exception as error:  # noqa: BLE001 - 每日隔离，继续后续日期
                failed.append(data_date)
                errors.append(BackfillError(data_date, _safe_error_type(error)))
                if not isinstance(error, ClaimLost):
                    self._safe_record_failure(
                        store_id,
                        data_date,
                        _safe_error_type(error),
                        attempted_at,
                        errors,
                        claim_token,
                    )
            finally:
                try:
                    self.repository.release_collection_claim(
                        store_id, data_date, claim_token
                    )
                except Exception:  # noqa: BLE001 - 释放失败不能阻塞后续日期
                    errors.append(BackfillError(data_date, "persistence_failure"))

        return BackfillResult(
            requested_dates=tuple(requested_dates),
            successful_dates=tuple(successful),
            no_data_dates=tuple(no_data),
            failed_dates=tuple(failed),
            errors=tuple(errors),
            metrics_count=metrics_count,
            snapshots_count=snapshots_count,
        )

    def _safe_record_failure(
        self,
        store_id: str,
        data_date: date,
        note: str,
        attempted_at: datetime,
        errors: list[BackfillError],
        claim_token: str,
    ) -> None:
        """尽力记录失败，仓储异常不能阻塞后续业务日。"""
        try:
            self.repository.record_collection_failure(
                store_id,
                data_date,
                note=note,
                attempted_at=attempted_at,
                claim_token=claim_token,
            )
        except ClaimLost:
            return
        except Exception:  # noqa: BLE001 - 记录失败也必须隔离
            errors.append(BackfillError(data_date, "persistence_failure"))


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _dedupe_metrics(values: object) -> tuple[object, ...]:
    unique: dict[tuple[object, object, object], object] = {}
    for metric in tuple(values):
        key = (metric.store_id, metric.campaign_id, metric.item_id, metric.data_date)
        unique.setdefault(key, metric)
    return tuple(unique.values())


def _dedupe_snapshots(values: object) -> tuple[object, ...]:
    unique: dict[tuple[object, object, object], object] = {}
    for snapshot in tuple(values):
        key = (snapshot.store_id, snapshot.campaign_id, snapshot.observed_at)
        unique.setdefault(key, snapshot)
    return tuple(unique.values())


def _should_request(
    day: object, as_of: date, recheck_days: int, timezone: ZoneInfo
) -> bool:
    if day is None:
        return True
    status = getattr(day, "status", None)
    if status in {"success", "failed"}:
        return status == "failed"
    if status != "no_data":
        return True
    next_retry_at = getattr(day, "next_retry_at", None)
    if isinstance(next_retry_at, datetime):
        return as_of >= next_retry_at.astimezone(timezone).date()
    attempted_at = getattr(day, "attempted_at", None)
    if not isinstance(attempted_at, datetime):
        return True
    return as_of >= attempted_at.astimezone(timezone).date() + timedelta(
        days=recheck_days
    )


def _safe_error_type(error: Exception) -> str:
    if isinstance(error, ClaimLost):
        return "claim_lost"
    if isinstance(error, (TypeError, ValueError)):
        return "contract_error"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, RuntimeError):
        return "collector_error"
    return "unexpected_error"


def _require_positive_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")

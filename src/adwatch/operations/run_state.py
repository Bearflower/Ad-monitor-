from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "CREDENTIAL",
    "OAUTH",
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "WEBHOOK",
)
_WEBHOOK_PATTERN = re.compile(
    r"(https?://[^\s\"']*/hook/)[^\s\"'?]+",
    re.IGNORECASE,
)
_URL_QUERY_PATTERN = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']+")
_SECRET_FIELD_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|password|secret|signature|token)=([^\s,;]+)"
)


@dataclass(frozen=True)
class Incident:
    run_id: str
    occurred_at: str
    execution_date: str
    report_date: str
    stage: str
    error_type: str
    message: str
    exit_code: int
    retryable: bool = True
    retry_count: int = 0
    report_path: str | None = None
    notification_status: str = "pending"

    @property
    def dedupe_key(self) -> str:
        return f"{self.report_date}:{self.stage}:{self.error_type}"


class RunLockUnavailable(RuntimeError):
    pass


class RunStateStore:
    def __init__(self, data_dir: Path) -> None:
        self.operations_dir = data_dir / "operations"
        self.state_path = (
            self.operations_dir / "daily-schedule-state.json"
        )
        self.lock_path = self.operations_dir / "scheduled-daily.lock"
        self.weekly_lock_path = self.operations_dir / "scheduled-weekly.lock"
        self.fault_log = data_dir / "logs" / "adwatch-faults.jsonl"

    @contextmanager
    def _lock(self, lock_path: Path, label: str = "run") -> Iterator[None]:
        """获取指定路径的排他锁（非阻塞）。

        Args:
            lock_path: 锁文件路径
            label: 锁的描述标签，用于错误消息

        Raises:
            RunLockUnavailable: 锁已被其他进程持有
        """
        self.operations_dir.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as error:
                raise RunLockUnavailable(
                    f"scheduled {label} is already active"
                ) from error
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @contextmanager
    def lock(self) -> Iterator[None]:
        """获取日报调度的排他锁。"""
        with self._lock(self.lock_path, "daily run"):
            yield

    @contextmanager
    def weekly_lock(self) -> Iterator[None]:
        """获取周报调度的排他锁（与日报锁隔离）。"""
        with self._lock(self.weekly_lock_path, "weekly run"):
            yield

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("daily schedule state must be a JSON object")
        return loaded

    def write_state(self, state: dict[str, Any]) -> None:
        self.operations_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                state,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)

    def record_incident(self, incident: Incident) -> None:
        self.fault_log.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(incident)
        payload["message"] = redact_sensitive_text(incident.message)
        payload["dedupe_key"] = incident.dedupe_key
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode()
        descriptor = os.open(
            self.fault_log,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)

    def should_notify(self, incident: Incident) -> bool:
        notified = self.read_state().get("notified_incidents", {})
        return not isinstance(notified, dict) or (
            incident.dedupe_key not in notified
        )

    def mark_notified(self, incident: Incident) -> None:
        state = self.read_state()
        notified = state.get("notified_incidents")
        if not isinstance(notified, dict):
            notified = {}
        notified[incident.dedupe_key] = incident.occurred_at
        state["notified_incidents"] = notified
        self.write_state(state)

    def last_successful_report_date(self) -> date | None:
        raw = self.read_state().get("last_successful_report_date")
        if not isinstance(raw, str):
            return None
        return date.fromisoformat(raw)

    def record_noop(
        self,
        reason: str,
        current: datetime,
        report_date: date,
    ) -> None:
        state = self.read_state()
        state["last_run"] = {
            "status": "noop",
            "reason": reason,
            "execution_date": current.date().isoformat(),
            "report_date": report_date.isoformat(),
            "finished_at": current.isoformat(),
            "exit_code": 0,
        }
        self.write_state(state)

    def record_result(
        self,
        current: datetime,
        report_date: date,
        result: Any,
    ) -> None:
        state = self.read_state()
        state["last_run"] = {
            "status": result.status,
            "execution_date": current.date().isoformat(),
            "report_date": report_date.isoformat(),
            "finished_at": current.isoformat(),
            "exit_code": result.exit_code,
            "report_path": (
                str(result.report_path) if result.report_path else None
            ),
            "collection_errors": list(result.collection_errors),
        }
        if result.status == "ok" and result.exit_code == 0:
            state["last_successful_report_date"] = report_date.isoformat()
        self.write_state(state)

    def last_successful_weekly_date(self) -> date | None:
        """获取最近一次成功生成的周报的周日日期。"""
        raw = self.read_state().get("last_successful_weekly_date")
        if not isinstance(raw, str):
            return None
        return date.fromisoformat(raw)

    def record_weekly_result(
        self,
        current: datetime,
        week_end: date,
        result: Any,
    ) -> None:
        """记录周报运行结果。

        Args:
            current: 当前时间
            week_end: 周报截止日期（周日）
            result: 交付结果
        """
        state = self.read_state()
        state["last_successful_weekly_date"] = week_end.isoformat()
        state["last_weekly_run"] = {
            "status": result.status,
            "week_end": week_end.isoformat(),
            "finished_at": current.isoformat(),
            "report_path": (
                str(result.path) if hasattr(result, "path") and result.path else None
            ),
        }
        self.write_state(state)

    def last_successful_monthly_date(self) -> date | None:
        """获取最近一次成功生成的月报的月份日期（当月1日）。"""
        raw = self.read_state().get("last_successful_monthly_date")
        if not isinstance(raw, str):
            return None
        return date.fromisoformat(raw)

    def record_monthly_result(
        self,
        current: datetime,
        month_first: date,
        result: Any,
    ) -> None:
        """记录月报运行结果。

        Args:
            current: 当前时间
            month_first: 月报所属月份的第一天
            result: 交付结果
        """
        state = self.read_state()
        state["last_successful_monthly_date"] = month_first.isoformat()
        state["last_monthly_run"] = {
            "status": result.status,
            "month_first": month_first.isoformat(),
            "finished_at": current.isoformat(),
            "report_path": (
                str(result.path) if hasattr(result, "path") and result.path else None
            ),
        }
        self.write_state(state)


def redact_sensitive_text(message: str) -> str:
    redacted = _WEBHOOK_PATTERN.sub(r"\1[REDACTED]", message)
    redacted = _URL_QUERY_PATTERN.sub(r"\1?[REDACTED]", redacted)
    redacted = _SECRET_FIELD_PATTERN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        redacted,
    )
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 4
            and any(marker in name.upper() for marker in _SENSITIVE_ENV_MARKERS)
        ):
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted

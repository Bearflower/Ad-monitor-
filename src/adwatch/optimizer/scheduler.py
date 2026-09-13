"""唯一 Shopee 优化器 LaunchAgent 的渲染与本机管理。"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from adwatch.config import Settings

LAUNCH_AGENT_LABEL = "com.adwatch.daily"
LAUNCH_AGENT_FILENAME = f"{LAUNCH_AGENT_LABEL}.plist"
_REQUIRED_TIMEZONE = "Asia/Shanghai"
_REQUIRED_RUN_TIME = "09:00"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, arguments: list[str]) -> CommandResult: ...


class SubprocessCommandRunner:
    """不经过 shell 调用固定参数的系统命令。"""

    def run(self, arguments: list[str]) -> CommandResult:
        completed = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


@dataclass(frozen=True)
class ScheduleStatus:
    installed: bool
    loaded: bool
    disabled: bool
    issues: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return self.installed and self.loaded and not self.disabled and not self.issues


class ScheduleCommandError(RuntimeError):
    """安全、稳定的调度命令错误，不包含底层命令输出。"""


def render_launchd_plist(project_dir: Path, settings: Settings) -> bytes:
    """渲染唯一的北京时间 09:00 每日任务。"""
    project = Path(project_dir).resolve()
    data_dir = _absolute_data_dir(project, settings.data_dir)
    executable = project / ".venv" / "bin" / "adwatch"
    command_path = ":".join(
        (
            str(executable.parent),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        )
    )
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(executable),
            "run",
            "daily",
        ],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": {
            "ADWATCH_TIMEZONE": settings.timezone,
            "TZ": settings.timezone,
            "PATH": command_path,
        },
        "StartCalendarInterval": {
            "Hour": settings.daily_run_hour,
            "Minute": settings.daily_run_minute,
        },
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(data_dir / "logs" / "launchd.out.log"),
        "StandardErrorPath": str(data_dir / "logs" / "launchd.err.log"),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


class ScheduleManager:
    def __init__(
        self,
        *,
        project_dir: Path,
        settings: Settings,
        doctor: Callable[[], tuple[tuple[str, bool], ...]],
        launch_agents_dir: Path | None = None,
        command_runner: CommandRunner | None = None,
        user_id: int | None = None,
        system_timezone: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.settings = settings
        self.doctor = doctor
        self.launch_agents_dir = (
            Path.home() / "Library" / "LaunchAgents"
            if launch_agents_dir is None
            else Path(launch_agents_dir)
        )
        self.command_runner = command_runner or SubprocessCommandRunner()
        self.user_id = os.getuid() if user_id is None else user_id
        self.system_timezone = system_timezone or detect_system_timezone()
        self.clock = clock
        self.executable = self.project_dir / ".venv" / "bin" / "adwatch"
        self.target_plist = self.launch_agents_dir / LAUNCH_AGENT_FILENAME
        self.backup_dir = (
            _absolute_data_dir(self.project_dir, settings.data_dir)
            / "backups"
            / "launchagent"
        )

    @property
    def domain(self) -> str:
        return f"gui/{self.user_id}"

    @property
    def service_target(self) -> str:
        return f"{self.domain}/{LAUNCH_AGENT_LABEL}"

    def status(self) -> ScheduleStatus:
        return self._status()

    def install(self) -> ScheduleStatus:
        self._validate_install()
        expected = render_launchd_plist(self.project_dir, self.settings)
        original = (
            self.target_plist.read_bytes() if self.target_plist.is_file() else None
        )
        before = self.status()
        if before.healthy:
            return before

        wrote_plist = False
        try:
            needs_bootstrap = not before.loaded
            if before.loaded and (
                not before.installed or self.target_plist.read_bytes() != expected
            ):
                self._require_success(
                    self._run("bootout", self.service_target), "bootout"
                )
                needs_bootstrap = True
            _write_atomic(self.target_plist, expected)
            wrote_plist = True
            logs = (
                _absolute_data_dir(self.project_dir, self.settings.data_dir) / "logs"
            )
            logs.mkdir(parents=True, exist_ok=True)
            if before.disabled:
                self._require_success(
                    self._run("enable", self.service_target), "enable"
                )
            if needs_bootstrap:
                self._require_success(
                    self._run("bootstrap", self.domain, str(self.target_plist)),
                    "bootstrap",
                )
            after = self._status()
            if not after.healthy:
                raise ScheduleCommandError("launchctl verification failed")
            return after
        except Exception as error:
            if wrote_plist:
                self._restore_plist(original)
            if isinstance(error, ScheduleCommandError):
                raise
            raise ScheduleCommandError("schedule installation failed") from error

    def uninstall(self) -> ScheduleStatus:
        if not self.target_plist.exists():
            return self._status()
        expected = render_launchd_plist(self.project_dir, self.settings)
        try:
            current = self.target_plist.read_bytes()
        except OSError as error:
            raise ScheduleCommandError("unable to read schedule plist") from error
        if current != expected:
            raise ScheduleCommandError("refusing to remove unrecognized plist")

        self._backup(current)
        loaded = self._run("print", self.service_target).returncode == 0
        if loaded:
            self._require_success(
                self._run("bootout", self.service_target), "bootout"
            )
        try:
            self.target_plist.unlink()
        except OSError as error:
            raise ScheduleCommandError("unable to remove schedule plist") from error
        return self._status()

    def _status(self) -> ScheduleStatus:
        expected = render_launchd_plist(self.project_dir, self.settings)
        installed = self.target_plist.is_file()
        issues: list[str] = []
        if not installed:
            issues.append("plist_missing")
        else:
            try:
                if self.target_plist.read_bytes() != expected:
                    issues.append("configuration_drift")
            except OSError as error:
                raise ScheduleCommandError("unable to inspect schedule plist") from error

        loaded = self._run("print", self.service_target).returncode == 0
        if not loaded:
            issues.append("not_loaded")
        disabled = False
        result = self._run("print-disabled", self.domain)
        disabled = result.returncode != 0 or bool(
            re.search(
                rf'"?{re.escape(LAUNCH_AGENT_LABEL)}"?\s*=>\s*true',
                result.stdout,
            )
        )
        if disabled:
            issues.append("disabled")
        if self.system_timezone != self.settings.timezone:
            issues.append(f"timezone_mismatch:{self.system_timezone}")
        return ScheduleStatus(installed, loaded, disabled, tuple(issues))

    def _validate_install(self) -> None:
        if self.settings.timezone != _REQUIRED_TIMEZONE:
            raise ScheduleCommandError("schedule timezone must be Asia/Shanghai")
        if self.system_timezone != _REQUIRED_TIMEZONE:
            raise ScheduleCommandError("system timezone must be Asia/Shanghai")
        if self.settings.daily_run_time != _REQUIRED_RUN_TIME:
            raise ScheduleCommandError("schedule time must be 09:00")
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise ScheduleCommandError("runtime executable is unavailable")
        try:
            checks = self.doctor()
        except Exception as error:
            raise ScheduleCommandError("doctor validation failed") from error
        if not checks or not all(healthy for _, healthy in checks):
            raise ScheduleCommandError("doctor validation failed")

    def _run(self, *arguments: str) -> CommandResult:
        try:
            return self.command_runner.run(["/bin/launchctl", *arguments])
        except Exception as error:
            raise ScheduleCommandError("launchctl invocation failed") from error

    def _backup(self, content: bytes) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.clock().astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.backup_dir / f"{stamp}-{LAUNCH_AGENT_FILENAME}"
        _write_atomic(destination, content)
        try:
            shutil.copystat(self.target_plist, destination)
        except OSError:
            pass

    def _restore_plist(self, original: bytes | None) -> None:
        if original is not None:
            _write_atomic(self.target_plist, original)
            return
        try:
            self.target_plist.unlink(missing_ok=True)
        except OSError as error:
            raise ScheduleCommandError("unable to roll back schedule file") from error

    @staticmethod
    def _require_success(result: CommandResult, operation: str) -> None:
        if result.returncode != 0:
            raise ScheduleCommandError(f"launchctl {operation} failed")


def _absolute_data_dir(project_dir: Path, data_dir: Path) -> Path:
    return data_dir if data_dir.is_absolute() else project_dir / data_dir


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except OSError as error:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise ScheduleCommandError("unable to write schedule file") from error


def detect_system_timezone(
    candidates: tuple[Path, ...] = (
        Path("/etc/localtime"),
        Path("/var/db/timezone/localtime"),
    ),
) -> str:
    """优先由 zoneinfo 链接取得无歧义的 IANA 时区名称。"""
    for candidate in candidates:
        try:
            parts = candidate.resolve(strict=True).parts
        except OSError:
            continue
        if "zoneinfo" in parts:
            position = parts.index("zoneinfo")
            return "/".join(parts[position + 1 :])
    timezone = datetime.now().astimezone().tzinfo
    key = getattr(timezone, "key", None)
    return key if isinstance(key, str) else str(timezone)

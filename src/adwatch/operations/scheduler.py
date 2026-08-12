from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Protocol

from adwatch.config import Settings

LAUNCH_AGENT_LABEL = "com.adwatch.daily"
LAUNCH_AGENT_FILENAME = f"{LAUNCH_AGENT_LABEL}.plist"
LAUNCH_AGENT_LABEL_WEEKLY = "com.adwatch.weekly"
WEEKLY_PLIST_FILENAME = f"{LAUNCH_AGENT_LABEL_WEEKLY}.plist"
LEGACY_PROJECT_REFERENCES = (
    "/Users/yl/Documents/跨境电商/广告盯盘自动化",
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Launchctl(Protocol):
    def run(self, *arguments: str) -> CommandResult: ...


class SubprocessLaunchctl:
    def run(self, *arguments: str) -> CommandResult:
        completed = subprocess.run(
            ["launchctl", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


@dataclass(frozen=True)
class ScheduleStatus:
    healthy: bool
    loaded: bool
    enabled: bool
    project_dir: Path
    issues: tuple[str, ...]


class ScheduleCommandError(RuntimeError):
    pass


def detect_system_timezone() -> str:
    for candidate in (
        Path("/etc/localtime"),
        Path("/var/db/timezone/localtime"),
    ):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        parts = resolved.parts
        if "zoneinfo" in parts:
            position = parts.index("zoneinfo")
            return "/".join(parts[position + 1 :])
    timezone = datetime.now().astimezone().tzinfo
    key = getattr(timezone, "key", None)
    return key if isinstance(key, str) else str(timezone)


def render_launchd_plist(project_dir: Path, settings: Settings) -> str:
    project_dir = project_dir.resolve()
    data_dir = (
        settings.data_dir
        if settings.data_dir.is_absolute()
        else project_dir / settings.data_dir
    )
    executable = project_dir / ".venv" / "bin" / "adwatch"
    stdout = data_dir / "logs" / "launchd.out.log"
    stderr = data_dir / "logs" / "launchd.err.log"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        f"<key>Label</key><string>{LAUNCH_AGENT_LABEL}</string>"
        "<key>ProgramArguments</key><array>"
        f"<string>{escape(str(executable))}</string>"
        "<string>run</string><string>scheduled-daily</string></array>"
        f"<key>WorkingDirectory</key><string>{escape(str(project_dir))}</string>"
        "<key>EnvironmentVariables</key><dict>"
        "<key>PATH</key>"
        "<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>"
        f"<key>ADWATCH_TIMEZONE</key><string>{escape(settings.timezone)}</string>"
        f"<key>TZ</key><string>{escape(settings.timezone)}</string>"
        "</dict>"
        f"<key>StandardOutPath</key><string>{escape(str(stdout))}</string>"
        f"<key>StandardErrorPath</key><string>{escape(str(stderr))}</string>"
        "<key>StartCalendarInterval</key><dict>"
        f"<key>Hour</key><integer>{settings.daily_run_hour}</integer>"
        f"<key>Minute</key><integer>{settings.daily_run_minute}</integer>"
        "</dict>"
        "<key>ProcessType</key><string>Background</string>"
        "<key>RunAtLoad</key><true/>"
        "</dict></plist>"
    )


class ScheduleManager:
    def __init__(
        self,
        *,
        project_dir: Path,
        settings: Settings,
        launch_agents_dir: Path | None = None,
        launchctl: Launchctl | None = None,
        user_id: int | None = None,
        system_timezone: str | None = None,
    ) -> None:
        self.project_dir = project_dir.resolve()
        self.settings = settings
        self.launch_agents_dir = (
            launch_agents_dir
            or Path.home() / "Library" / "LaunchAgents"
        )
        self.launchctl = launchctl or SubprocessLaunchctl()
        self.user_id = os.getuid() if user_id is None else user_id
        self.system_timezone = (
            detect_system_timezone()
            if system_timezone is None
            else system_timezone
        )
        data_dir = (
            settings.data_dir
            if settings.data_dir.is_absolute()
            else self.project_dir / settings.data_dir
        )
        self.source_plist = data_dir / LAUNCH_AGENT_FILENAME
        self.target_plist = self.launch_agents_dir / LAUNCH_AGENT_FILENAME
        self.weekly_source_plist = data_dir / WEEKLY_PLIST_FILENAME
        self.weekly_target_plist = (
            self.launch_agents_dir / WEEKLY_PLIST_FILENAME
        )
        self.backup_dir = data_dir / "backups" / "launchagent"
        self.executable = (
            self.project_dir / ".venv" / "bin" / "adwatch"
        )

    @property
    def domain(self) -> str:
        return f"gui/{self.user_id}"

    @property
    def service_target(self) -> str:
        return f"{self.domain}/{LAUNCH_AGENT_LABEL}"

    @property
    def weekly_service_target(self) -> str:
        return f"{self.domain}/{LAUNCH_AGENT_LABEL_WEEKLY}"

    def _check_agent(
        self, label: str, plist_path: Path, service_target: str,
    ) -> dict[str, bool]:
        """检查单个 LaunchAgent 的运行状态。

        Returns:
            包含 loaded/enabled 和 plist_exists 的字典
        """
        expected = self._render_plist(label, "daily")
        plist_exists = plist_path.is_file()
        plist_matches = (
            plist_path.is_file()
            and plist_path.read_text(encoding="utf-8") == expected
        )
        loaded = (
            self.launchctl.run("print", service_target).returncode == 0
        )
        disabled_result = self.launchctl.run(
            "print-disabled", self.domain
        )
        enabled = disabled_result.returncode == 0 and not re.search(
            rf'"?{re.escape(label)}"?\s*=>\s*true',
            disabled_result.stdout,
        )
        return {
            "loaded": loaded,
            "enabled": enabled,
            "plist_exists": plist_exists,
            "plist_matches": plist_matches,
        }

    def status(self) -> ScheduleStatus:
        expected = render_launchd_plist(self.project_dir, self.settings)
        issues: list[str] = []
        if not self.target_plist.is_file():
            issues.append("plist_missing")
        elif self.target_plist.read_text(encoding="utf-8") != expected:
            issues.append("configuration_drift")

        loaded = (
            self.launchctl.run("print", self.service_target).returncode == 0
        )
        if not loaded:
            issues.append("not_loaded")

        disabled_result = self.launchctl.run(
            "print-disabled", self.domain
        )
        enabled = disabled_result.returncode == 0 and not re.search(
            rf'"?{re.escape(LAUNCH_AGENT_LABEL)}"?\s*=>\s*true',
            disabled_result.stdout,
        )
        if not enabled:
            issues.append("disabled")
        if self.system_timezone != self.settings.timezone:
            issues.append(f"timezone_mismatch:{self.system_timezone}")

        # 检查周报 agent 状态
        weekly_loaded = (
            self.launchctl.run(
                "print", self.weekly_service_target
            ).returncode == 0
        )
        if not weekly_loaded:
            issues.append("weekly_not_loaded")

        for legacy in self._legacy_plists():
            issues.append(f"legacy_reference:{legacy.name}")

        return ScheduleStatus(
            healthy=not issues,
            loaded=loaded,
            enabled=enabled,
            project_dir=self.project_dir,
            issues=tuple(issues),
        )

    def _render_plist(
        self, label: str, schedule_type: str = "daily",
    ) -> str:
        """生成指定类型的 LaunchAgent plist XML。

        Args:
            label: LaunchAgent 标签名
            schedule_type: 调度类型，'daily' 或 'weekly'

        Returns:
            plist XML 字符串
        """
        project_dir = self.project_dir.resolve()
        data_dir = (
            self.settings.data_dir
            if self.settings.data_dir.is_absolute()
            else project_dir / self.settings.data_dir
        )
        executable = str(project_dir / ".venv" / "bin" / "adwatch")
        stdout = str(data_dir / "logs" / "launchd.out.log")
        stderr = str(data_dir / "logs" / "launchd.err.log")

        subcommand = "scheduled-daily"
        if schedule_type == "weekly":
            subcommand = "scheduled-weekly"

        calendar = (
            f"<key>Hour</key><integer>{self.settings.daily_run_hour}</integer>"
            f"<key>Minute</key><integer>{self.settings.daily_run_minute}</integer>"
        )
        if schedule_type == "weekly":
            calendar += "<key>Weekday</key><integer>1</integer>"

        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            f"<key>Label</key><string>{escape(label)}</string>"
            "<key>ProgramArguments</key><array>"
            f"<string>{escape(executable)}</string>"
            f"<string>run</string><string>{escape(subcommand)}</string></array>"
            f"<key>WorkingDirectory</key><string>{escape(str(project_dir))}</string>"
            "<key>EnvironmentVariables</key><dict>"
            "<key>PATH</key>"
            "<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>"
            f"<key>ADWATCH_TIMEZONE</key><string>{escape(self.settings.timezone)}</string>"
            f"<key>TZ</key><string>{escape(self.settings.timezone)}</string>"
            "</dict>"
            f"<key>StandardOutPath</key><string>{escape(stdout)}</string>"
            f"<key>StandardErrorPath</key><string>{escape(stderr)}</string>"
            "<key>StartCalendarInterval</key><dict>"
            f"{calendar}"
            "</dict>"
            "<key>ProcessType</key><string>Background</string>"
            "<key>RunAtLoad</key><true/>"
            "</dict></plist>"
        )

    def _install_plist(
        self,
        label: str,
        plist_filename: str,
        source_path: Path,
        target_path: Path,
        schedule_type: str = "daily",
    ) -> None:
        """安装指定类型的 plist 到 LaunchAgents 目录。

        Args:
            label: LaunchAgent 标签
            plist_filename: plist 文件名
            source_path: 项目内源路径
            target_path: LaunchAgents 目标路径
            schedule_type: 调度类型
        """
        desired = self._render_plist(label, schedule_type)
        self._write_atomic(source_path, desired)
        if target_path.is_file():
            self._backup(target_path)
        self._write_atomic(target_path, desired)
        service_target = f"{self.domain}/{label}"
        self.launchctl.run("bootout", service_target)
        self._require_success(
            self.launchctl.run("enable", service_target),
            "enable",
        )
        self._require_success(
            self.launchctl.run(
                "bootstrap", self.domain, str(target_path),
            ),
            "bootstrap",
        )

    def install(
        self, *, include_weekly: bool = False,
    ) -> ScheduleStatus:
        if self.system_timezone != self.settings.timezone:
            raise ScheduleCommandError(
                "system timezone "
                f"{self.system_timezone} does not match "
                f"{self.settings.timezone}"
            )
        if not self.executable.is_file() or not os.access(
            self.executable, os.X_OK
        ):
            raise ScheduleCommandError(
                f"runtime executable is unavailable: {self.executable}"
            )
        desired = render_launchd_plist(self.project_dir, self.settings)
        self._remove_legacy_agents()
        if (
            self.target_plist.is_file()
            and self.target_plist.read_text(encoding="utf-8") == desired
        ):
            current = self.status()
            if current.healthy:
                self._write_atomic(self.source_plist, desired)
                if include_weekly:
                    self._install_weekly_plist()
                return current

        self._write_atomic(self.source_plist, desired)
        if self.target_plist.is_file():
            self._backup(self.target_plist)
        self._write_atomic(self.target_plist, desired)
        self.launchctl.run("bootout", self.service_target)
        self._require_success(
            self.launchctl.run("enable", self.service_target),
            "enable",
        )
        self._require_success(
            self.launchctl.run(
                "bootstrap",
                self.domain,
                str(self.target_plist),
            ),
            "bootstrap",
        )

        if include_weekly:
            self._install_weekly_plist()

        return self.status()

    def _install_weekly_plist(self) -> None:
        """安装周报 LaunchAgent。"""
        self._install_plist(
            LAUNCH_AGENT_LABEL_WEEKLY,
            WEEKLY_PLIST_FILENAME,
            self.weekly_source_plist,
            self.weekly_target_plist,
            schedule_type="weekly",
        )

    def repair(self) -> ScheduleStatus:
        return self.install(include_weekly=True)

    def uninstall(self) -> ScheduleStatus:
        # 卸载日报调度
        if self.target_plist.is_file():
            if (
                not self._is_recognized_adwatch_plist(self.target_plist)
                or self._plist_label(self.target_plist)
                != LAUNCH_AGENT_LABEL
            ):
                current = self.status()
                return ScheduleStatus(
                    healthy=False,
                    loaded=current.loaded,
                    enabled=current.enabled,
                    project_dir=current.project_dir,
                    issues=(*current.issues, "unrecognized_plist"),
                )
            self._backup(self.target_plist)
            self.launchctl.run("bootout", self.service_target)
            self.target_plist.unlink()

        # 卸载周报调度
        if (
            self.weekly_target_plist.is_file()
            and self._is_recognized_adwatch_plist(
                self.weekly_target_plist
            )
        ):
            self._backup(self.weekly_target_plist)
            self.launchctl.run(
                "bootout", self.weekly_service_target
            )
            self.weekly_target_plist.unlink()

        return self.status()

    def _legacy_plists(self) -> list[Path]:
        if not self.launch_agents_dir.is_dir():
            return []
        matches = []
        for path in self.launch_agents_dir.glob("*.plist"):
            try:
                contents = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(
                reference in contents
                for reference in LEGACY_PROJECT_REFERENCES
            ) and self._is_recognized_adwatch_plist(path):
                matches.append(path)
        return matches

    def _remove_legacy_agents(self) -> None:
        for path in self._legacy_plists():
            label = self._plist_label(path)
            self._backup(path)
            self.launchctl.run("bootout", f"{self.domain}/{label}")
            path.unlink()

    def _is_recognized_adwatch_plist(self, path: Path) -> bool:
        try:
            payload = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            return False
        if not isinstance(payload, dict):
            return False
        label = payload.get("Label")
        arguments = payload.get("ProgramArguments", [])
        return (
            isinstance(label, str)
            and label.startswith("com.adwatch.")
            and isinstance(arguments, list)
            and any(
                isinstance(argument, str)
                and Path(argument).name == "adwatch"
                for argument in arguments
            )
        )

    @staticmethod
    def _plist_label(path: Path) -> str:
        payload = plistlib.loads(path.read_bytes())
        if not isinstance(payload, dict):
            raise TypeError("Adwatch LaunchAgent plist must be a dictionary")
        label = payload.get("Label")
        if not isinstance(label, str):
            raise TypeError("Adwatch LaunchAgent label is missing")
        return label

    def _backup(self, path: Path) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, self.backup_dir / f"{stamp}-{path.name}")

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _require_success(result: CommandResult, operation: str) -> None:
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ScheduleCommandError(
                f"launchctl {operation} failed: {detail}"
            )

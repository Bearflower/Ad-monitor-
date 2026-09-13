from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adwatch.config import Settings
from adwatch.optimizer.scheduler import (
    LAUNCH_AGENT_LABEL,
    CommandResult,
    ScheduleCommandError,
    ScheduleManager,
    SubprocessCommandRunner,
    detect_system_timezone,
    render_launchd_plist,
)


class FakeRunner:
    def __init__(
        self,
        *,
        loaded: bool = False,
        disabled: bool = False,
        bootout_code: int = 0,
        bootstrap_code: int = 0,
    ) -> None:
        self.loaded = loaded
        self.disabled = disabled
        self.bootout_code = bootout_code
        self.bootstrap_code = bootstrap_code
        self.calls: list[list[str]] = []
        self.before_bootout = None

    def run(self, arguments: list[str]) -> CommandResult:
        assert isinstance(arguments, list)
        self.calls.append(arguments)
        action = arguments[1]
        if action == "print":
            return CommandResult(0 if self.loaded else 113)
        if action == "print-disabled":
            value = "true" if self.disabled else "false"
            return CommandResult(0, f'{{"{LAUNCH_AGENT_LABEL}" => {value}}}')
        if action == "bootout":
            if self.before_bootout is not None:
                self.before_bootout()
            if self.bootout_code == 0:
                self.loaded = False
            return CommandResult(self.bootout_code)
        if action == "bootstrap":
            if self.bootstrap_code == 0:
                self.loaded = True
            return CommandResult(self.bootstrap_code)
        if action == "enable":
            self.disabled = False
        return CommandResult(0)


def _doctor(*, healthy: bool = True):
    def check() -> tuple[tuple[str, bool], ...]:
        return (
            ("runtime", healthy),
            ("database", healthy),
            ("ziniao_cli", healthy),
            ("shopee_store", healthy),
            ("local_port", healthy),
        )

    return check


def _manager(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
    doctor=None,
    timezone: str = "Asia/Shanghai",
    run_time: str = "09:00",
    executable: bool = True,
) -> ScheduleManager:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    runtime = project / ".venv" / "bin" / "adwatch"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if executable:
        runtime.write_text("#!/bin/sh\n", encoding="utf-8")
        runtime.chmod(0o755)
    settings = Settings(
        data_dir=project / "var",
        timezone=timezone,
        daily_run_time=run_time,
    )
    return ScheduleManager(
        project_dir=project,
        settings=settings,
        doctor=_doctor() if doctor is None else doctor,
        launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
        command_runner=runner or FakeRunner(),
        user_id=501,
        system_timezone="Asia/Shanghai",
        clock=lambda: datetime(2026, 9, 11, 1, 2, 3, 456789, tzinfo=UTC),
    )


def test_plist_is_one_beijing_0900_daily_job_with_absolute_paths(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    payload = plistlib.loads(
        render_launchd_plist(manager.project_dir, manager.settings)
    )

    assert payload["Label"] == LAUNCH_AGENT_LABEL == "com.adwatch.daily"
    assert payload["ProgramArguments"] == [
        str(manager.executable),
        "run",
        "daily",
    ]
    assert Path(payload["ProgramArguments"][0]).is_absolute()
    assert payload["WorkingDirectory"] == str(manager.project_dir)
    assert Path(payload["WorkingDirectory"]).is_absolute()
    environment = payload["EnvironmentVariables"]
    assert environment == {
        "ADWATCH_TIMEZONE": "Asia/Shanghai",
        "TZ": "Asia/Shanghai",
        "PATH": (
            f"{manager.executable.parent}:"
            "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        ),
    }
    local_cli = manager.executable.parent / "ziniao-cli"
    local_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    local_cli.chmod(0o755)
    assert shutil.which("ziniao-cli", path=environment["PATH"]) == str(local_cli)
    assert payload["StartCalendarInterval"] == {"Hour": 9, "Minute": 0}
    assert payload["RunAtLoad"] is True
    assert payload["StandardOutPath"] == str(
        manager.settings.data_dir / "logs" / "launchd.out.log"
    )
    assert payload["StandardErrorPath"] == str(
        manager.settings.data_dir / "logs" / "launchd.err.log"
    )
    assert "weekly" not in repr(payload).lower()


def test_system_timezone_uses_zoneinfo_symlink_instead_of_ambiguous_cst(
    tmp_path: Path,
) -> None:
    zone = tmp_path / "zoneinfo" / "Asia" / "Shanghai"
    zone.parent.mkdir(parents=True)
    zone.touch()
    localtime = tmp_path / "localtime"
    localtime.symlink_to(zone)

    assert detect_system_timezone((localtime,)) == "Asia/Shanghai"


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"timezone": "Etc/UTC"}, "Asia/Shanghai"),
        ({"run_time": "08:30"}, "09:00"),
        ({"executable": False}, "executable"),
        ({"doctor": _doctor(healthy=False)}, "doctor"),
    ],
)
def test_install_validates_schedule_runtime_and_doctor_before_writes(
    tmp_path: Path, changes: dict[str, object], match: str
) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner=runner, **changes)

    with pytest.raises(ScheduleCommandError, match=match):
        manager.install()

    assert not manager.target_plist.exists()
    assert runner.calls == []


def test_install_bootstraps_once_and_repeated_install_is_idempotent(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner=runner)

    first = manager.install()
    installed = manager.target_plist.read_bytes()
    runner.calls.clear()
    second = manager.install()

    assert first.healthy and second.healthy
    assert manager.target_plist.read_bytes() == installed
    assert [call[1] for call in runner.calls] == ["print", "print-disabled"]
    assert all(call[0] == "/bin/launchctl" for call in runner.calls)


def test_status_reports_missing_drift_and_load_state_without_writing(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner=runner)
    missing = manager.status()
    manager.target_plist.parent.mkdir(parents=True)
    manager.target_plist.write_text("not a plist", encoding="utf-8")
    drift = manager.status()

    assert not missing.installed and "plist_missing" in missing.issues
    assert not drift.healthy and "configuration_drift" in drift.issues
    assert not drift.loaded and "not_loaded" in drift.issues
    assert manager.target_plist.read_text(encoding="utf-8") == "not a plist"


def test_status_detects_disabled_job_and_install_reenables_it(tmp_path: Path) -> None:
    runner = FakeRunner(loaded=True, disabled=True)
    manager = _manager(tmp_path, runner=runner)
    manager.target_plist.parent.mkdir(parents=True)
    manager.target_plist.write_bytes(
        render_launchd_plist(manager.project_dir, manager.settings)
    )

    before = manager.status()
    runner.calls.clear()
    after = manager.install()

    assert not before.healthy and "disabled" in before.issues
    assert after.healthy and not runner.disabled
    assert "enable" in [call[1] for call in runner.calls]


def test_install_enables_disabled_job_before_bootstrap_when_not_loaded(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(loaded=False, disabled=True)
    manager = _manager(tmp_path, runner=runner)

    manager.install()

    actions = [call[1] for call in runner.calls]
    assert actions.index("enable") < actions.index("bootstrap")


def test_failed_bootstrap_removes_plist_created_by_current_install(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(bootstrap_code=5)
    manager = _manager(tmp_path, runner=runner)

    with pytest.raises(ScheduleCommandError, match="bootstrap"):
        manager.install()

    assert not manager.target_plist.exists()


def test_uninstall_refuses_unrecognized_plist_without_commands(tmp_path: Path) -> None:
    runner = FakeRunner(loaded=True)
    manager = _manager(tmp_path, runner=runner)
    manager.target_plist.parent.mkdir(parents=True)
    manager.target_plist.write_bytes(
        plistlib.dumps(
            {
                "Label": LAUNCH_AGENT_LABEL,
                "ProgramArguments": ["/tmp/adwatch", "run", "daily"],
                "WorkingDirectory": "/tmp",
            }
        )
    )
    original = manager.target_plist.read_bytes()

    with pytest.raises(ScheduleCommandError, match="unrecognized"):
        manager.uninstall()

    assert manager.target_plist.read_bytes() == original
    assert runner.calls == []
    assert not manager.backup_dir.exists()


def test_uninstall_backs_up_exact_plist_before_bootout_and_delete(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(loaded=True)
    manager = _manager(tmp_path, runner=runner)
    manager.target_plist.parent.mkdir(parents=True)
    desired = render_launchd_plist(manager.project_dir, manager.settings)
    manager.target_plist.write_bytes(desired)

    def assert_backup_exists_before_bootout() -> None:
        backups = tuple(manager.backup_dir.glob("*.plist"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == desired
        assert manager.target_plist.exists()

    runner.before_bootout = assert_backup_exists_before_bootout
    result = manager.uninstall()

    assert not result.installed and not result.loaded
    assert not manager.target_plist.exists()
    assert [call[1] for call in runner.calls] == [
        "print",
        "bootout",
        "print",
        "print-disabled",
    ]


def test_failed_bootout_keeps_recognized_plist_and_safe_backup(tmp_path: Path) -> None:
    runner = FakeRunner(loaded=True, bootout_code=5)
    manager = _manager(tmp_path, runner=runner)
    manager.target_plist.parent.mkdir(parents=True)
    manager.target_plist.write_bytes(
        render_launchd_plist(manager.project_dir, manager.settings)
    )

    with pytest.raises(ScheduleCommandError, match="bootout") as captured:
        manager.uninstall()

    assert manager.target_plist.exists()
    assert len(tuple(manager.backup_dir.glob("*.plist"))) == 1
    assert "secret" not in str(captured.value)


def test_subprocess_runner_passes_argument_list_without_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(arguments, **kwargs):
        observed["arguments"] = arguments
        observed.update(kwargs)
        return subprocess.CompletedProcess(arguments, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SubprocessCommandRunner().run(["/bin/launchctl", "print", "gui/501/x"])

    assert result == CommandResult(0, "ok", "")
    assert observed["arguments"] == ["/bin/launchctl", "print", "gui/501/x"]
    assert observed["shell"] is False
    assert observed["check"] is False


def test_runner_exception_is_reclassified_without_leaking_message(tmp_path: Path) -> None:
    class BrokenRunner:
        def run(self, arguments: list[str]) -> CommandResult:
            raise OSError("secret user path")

    manager = _manager(tmp_path)
    manager.command_runner = BrokenRunner()

    with pytest.raises(ScheduleCommandError) as captured:
        manager.status()

    assert "secret user path" not in str(captured.value)


def test_scheduler_import_and_status_never_install_or_uninstall(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner=runner)

    manager.status()

    assert {call[1] for call in runner.calls} == {"print", "print-disabled"}
    assert not manager.target_plist.exists()
    assert not manager.backup_dir.exists()
    assert os.path.basename(manager.target_plist) == "com.adwatch.daily.plist"

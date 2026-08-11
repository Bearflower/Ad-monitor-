import pytest

from adwatch.config import Settings
from adwatch.operations.scheduler import (
    CommandResult,
    ScheduleCommandError,
    ScheduleManager,
    render_launchd_plist,
)


class FakeLaunchctl:
    def __init__(self, *, loaded=True, disabled=False):
        self.calls = []
        self.loaded = loaded
        self.disabled = disabled

    def run(self, *arguments):
        self.calls.append(arguments)
        if arguments[0] == "print":
            return CommandResult(
                0 if self.loaded else 113,
                "service" if self.loaded else "",
                "" if self.loaded else "not found",
            )
        if arguments[0] == "print-disabled":
            value = "true" if self.disabled else "false"
            return CommandResult(
                0,
                f'{{"com.adwatch.daily" => {value}}}',
                "",
            )
        if arguments[0] == "bootstrap":
            self.loaded = True
        if arguments[0] == "enable":
            self.disabled = False
        return CommandResult(0, "", "")


def _manager(
    tmp_path,
    launchctl=None,
    *,
    system_timezone="Asia/Shanghai",
):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    executable = project_dir / ".venv" / "bin" / "adwatch"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    settings = Settings(
        data_dir=project_dir / "var",
        daily_run_time="09:00",
        timezone="Asia/Shanghai",
    )
    return ScheduleManager(
        project_dir=project_dir,
        settings=settings,
        launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
        launchctl=launchctl or FakeLaunchctl(),
        user_id=501,
        system_timezone=system_timezone,
    )


def test_rendered_plist_targets_current_project_and_scheduled_wrapper(
    tmp_path,
):
    settings = Settings(
        data_dir=tmp_path / "var",
        daily_run_time="09:00",
    )

    plist = render_launchd_plist(tmp_path, settings)

    assert str(tmp_path / ".venv/bin/adwatch") in plist
    assert "<string>scheduled-daily</string>" in plist
    assert "<key>Hour</key><integer>9</integer>" in plist
    assert "<key>Minute</key><integer>0</integer>" in plist
    assert "<key>RunAtLoad</key><true/>" in plist


def test_install_boots_out_enables_bootstraps_and_verifies(tmp_path):
    launchctl = FakeLaunchctl(loaded=False, disabled=True)
    manager = _manager(tmp_path, launchctl)

    status = manager.install()

    actions = [call[0] for call in launchctl.calls]
    assert actions[:4] == ["bootout", "enable", "bootstrap", "print"]
    assert status.healthy is True
    assert manager.target_plist.read_text() == manager.source_plist.read_text()


def test_repeated_healthy_install_does_not_bootstrap_again(tmp_path):
    launchctl = FakeLaunchctl(loaded=False)
    manager = _manager(tmp_path, launchctl)
    manager.install()
    launchctl.calls.clear()

    status = manager.install()

    assert status.healthy is True
    assert "bootstrap" not in [call[0] for call in launchctl.calls]
    assert "bootout" not in [call[0] for call in launchctl.calls]


def test_status_detects_drift_disabled_and_not_loaded(tmp_path):
    launchctl = FakeLaunchctl(loaded=False, disabled=True)
    manager = _manager(tmp_path, launchctl)
    manager.target_plist.parent.mkdir(parents=True)
    manager.target_plist.write_text(
        "<plist>/Users/yl/Documents/跨境电商/广告盯盘自动化</plist>"
    )

    status = manager.status()

    assert status.healthy is False
    assert {"configuration_drift", "disabled", "not_loaded"} <= set(
        status.issues
    )


def test_uninstall_refuses_unrecognized_plist(tmp_path):
    manager = _manager(tmp_path)
    manager.target_plist.parent.mkdir(parents=True)
    manager.target_plist.write_text(
        '<?xml version="1.0"?><plist><dict>'
        "<key>Label</key><string>com.adwatch.other</string>"
        "<key>ProgramArguments</key><array>"
        "<string>/tmp/adwatch</string></array>"
        "</dict></plist>"
    )

    status = manager.uninstall()

    assert manager.target_plist.exists()
    assert "unrecognized_plist" in status.issues


def test_uninstall_removes_only_recognized_adwatch_plist(tmp_path):
    launchctl = FakeLaunchctl()
    manager = _manager(tmp_path, launchctl)
    manager.target_plist.parent.mkdir(parents=True)
    manager.target_plist.write_text(
        render_launchd_plist(manager.project_dir, manager.settings)
    )

    manager.uninstall()

    assert not manager.target_plist.exists()
    assert launchctl.calls[0][0] == "bootout"


def test_install_refuses_missing_project_runtime(tmp_path):
    manager = _manager(tmp_path)
    manager.executable.unlink()

    with pytest.raises(ScheduleCommandError, match="runtime executable"):
        manager.install()

    assert not manager.target_plist.exists()


def test_wrong_system_timezone_is_unhealthy_and_install_is_refused(
    tmp_path,
):
    launchctl = FakeLaunchctl(loaded=False)
    manager = _manager(
        tmp_path,
        launchctl,
        system_timezone="Etc/UTC",
    )

    status = manager.status()

    assert "timezone_mismatch:Etc/UTC" in status.issues
    with pytest.raises(ScheduleCommandError, match="system timezone"):
        manager.install()
    assert not manager.target_plist.exists()
    assert "bootstrap" not in [call[0] for call in launchctl.calls]

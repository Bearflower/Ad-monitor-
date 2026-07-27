import adwatch.cli
from adwatch.cli import main
from adwatch.config import Settings


def test_cli_help_exits_successfully(capsys):
    assert main(["--help"]) == 0
    assert "collect" in capsys.readouterr().out


def test_doctor_uses_official_ziniao_cli_without_legacy_credentials(
    tmp_path, monkeypatch, capsys
):
    class FakeCliClient:
        def get_store_list(self):
            return [{"storeId": "1"}, {"storeId": "2"}]

    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(adwatch.cli.shutil, "which", lambda _: "/bin/ziniao-cli")
    monkeypatch.setattr(adwatch.cli, "ZiniaoCliClient", FakeCliClient)

    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "Ziniao CLI: reachable (2 stores)" in output


def test_collect_ziniao_uses_official_cli_store_configuration(
    tmp_path, monkeypatch, capsys
):
    class EmptyCollector:
        source = "ziniao"

        def __init__(self, settings, platform):
            self.platform = platform

        def collect(self, data_date):
            return []

    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "ZiniaoCollector", EmptyCollector)

    assert main(["collect", "--mode", "ziniao", "--date", "2026-07-22"]) == 0
    output = capsys.readouterr().out
    assert "tiktok: received=0" in output
    assert "shopee: received=0" in output


def test_schedule_plist_uses_real_mode_and_project_runtime(capsys):
    assert main(["schedule", "--print-launchd"]) == 0

    output = capsys.readouterr().out
    assert "<string>run</string><string>daily</string>" in output
    assert "<string>--mode</string><string>ziniao</string>" in output
    assert "<key>WorkingDirectory</key>" in output
    assert "/.venv/bin/adwatch</string>" in output
    assert "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" in output
    assert "<key>Hour</key><integer>8</integer>" in output


def test_backup_cli_creates_verified_snapshot(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0
    destination = tmp_path / "backup.sqlite3"

    assert main(["backup", "create", "--output", str(destination)]) == 0

    assert destination.exists()
    assert "integrity=ok" in capsys.readouterr().out


def test_readiness_cli_reports_pending_without_enabling_live(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        adwatch.cli, "_ziniao_bridge_ready", lambda: False
    )

    assert main(["readiness"]) == 2
    output = capsys.readouterr().out
    assert "ziniao_bridge=pending_external" in output
    assert "live_writes=blocked" in output


def test_monthly_report_cli_writes_local_file(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0

    assert main(["report", "monthly", "--month", "2026-07"]) == 0

    path = tmp_path / "reports" / "monthly-2026-07.md"
    assert path.exists()
    assert "# 广告月报 2026-07" in path.read_text()
    assert str(path) in capsys.readouterr().out


def test_approval_server_refuses_to_start_without_secret(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)

    assert main(["approval", "serve"]) == 2
    assert "FEISHU_CALLBACK_SECRET" in capsys.readouterr().out


def test_launch_checklist_cli_writes_markdown(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "_ziniao_bridge_ready", lambda: False)

    assert main(["launch-checklist", "--format", "markdown"]) == 0

    output = capsys.readouterr().out
    assert "# Adwatch 上线待办" in output
    assert "ziniao_bridge" in output
    assert "live_allowlist" in output


def test_weekly_report_cli_writes_local_file(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0

    assert main(["report", "weekly", "--end", "2026-07-26"]) == 0

    path = tmp_path / "reports" / "weekly-2026-07-26.md"
    assert path.exists()
    assert "# 广告周报" in path.read_text()


def test_daily_report_cli_writes_local_file(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0

    assert main(["report", "daily", "--date", "2026-07-26"]) == 0

    path = tmp_path / "reports" / "daily-2026-07-26.md"
    assert path.exists()
    assert "# 广告每日快报 2026-07-26" in path.read_text()
    assert str(path) in capsys.readouterr().out


def test_backup_verify_cli_rejects_corrupt_database(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not sqlite")

    assert main(["backup", "verify", "--path", str(path)]) == 2

    assert "verification failed" in capsys.readouterr().out.lower()


def test_live_execution_cli_refuses_before_page_access(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path, live_writes=False)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)

    assert (
        main(
            [
                "execute",
                "live",
                "--approval-id",
                "approval-1",
                "--idempotency-key",
                "run-1",
                "--expected-before",
                '{"budget":"100"}',
            ]
        )
        == 2
    )

    assert "ADWATCH_LIVE_WRITES is disabled" in capsys.readouterr().out


def test_shadow_execution_cli_runs_safe_executor(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    calls = {}

    class FakeResult:
        audit_id = "audit-1"
        status = "succeeded"

    class FakeExecutor:
        def __init__(self, database, backend):
            calls["backend_mode"] = backend.mode

        def execute(self, approval_id, *, idempotency_key, expected_before):
            calls.update(
                approval_id=approval_id,
                idempotency_key=idempotency_key,
                expected_before=expected_before,
            )
            return FakeResult()

    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "SafeExecutor", FakeExecutor)

    assert (
        main(
            [
                "execute",
                "shadow",
                "--approval-id",
                "approval-1",
                "--idempotency-key",
                "run-1",
                "--expected-before",
                '{"budget":"100"}',
            ]
        )
        == 0
    )

    assert calls == {
        "backend_mode": "shadow",
        "approval_id": "approval-1",
        "idempotency_key": "run-1",
        "expected_before": {"budget": "100"},
    }
    assert "audit=audit-1 status=succeeded" in capsys.readouterr().out

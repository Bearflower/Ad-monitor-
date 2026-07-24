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

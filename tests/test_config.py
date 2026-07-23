from adwatch.config import Settings


def test_settings_use_explicit_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.database_path == tmp_path / "adwatch.sqlite3"
    assert settings.report_dir == tmp_path / "reports"


def test_ziniao_readiness_requires_all_values(monkeypatch):
    monkeypatch.setenv("ZINIAO_COMPANY", "demo")
    assert Settings.from_env().ziniao_ready is False

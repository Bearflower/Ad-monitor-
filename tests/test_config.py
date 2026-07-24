from adwatch.config import Settings


def test_settings_use_explicit_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.database_path == tmp_path / "adwatch.sqlite3"
    assert settings.report_dir == tmp_path / "reports"


def test_ziniao_readiness_requires_all_values(monkeypatch):
    monkeypatch.setenv("ZINIAO_COMPANY", "demo")
    assert Settings.from_env().ziniao_ready is False


def test_official_ziniao_cli_settings_use_store_ids(monkeypatch):
    monkeypatch.setenv("ZINIAO_TIKTOK_STORE_ID", "111")
    monkeypatch.setenv("ZINIAO_SHOPEE_STORE_ID", "222")

    settings = Settings.from_env()

    assert settings.ziniao_tiktok_store_id == "111"
    assert settings.ziniao_shopee_store_id == "222"
    assert settings.ziniao_cli_ready is True


def test_settings_load_project_dotenv_without_overwriting_environment(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text(
        "ZINIAO_TIKTOK_STORE_ID=from-file\n"
        "ZINIAO_SHOPEE_STORE_ID=222\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZINIAO_TIKTOK_STORE_ID", "from-environment")
    monkeypatch.delenv("ZINIAO_SHOPEE_STORE_ID", raising=False)

    settings = Settings.from_env()

    assert settings.ziniao_tiktok_store_id == "from-environment"
    assert settings.ziniao_shopee_store_id == "222"

from adwatch.config import Settings


def test_settings_use_explicit_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ADWATCH_EVIDENCE_ROOT", raising=False)
    settings = Settings.from_env()
    assert settings.database_path == tmp_path / "adwatch.sqlite3"
    assert settings.report_dir == tmp_path / "reports"
    assert settings.evidence_dir == tmp_path / "evidence"


def test_evidence_directory_supports_explicit_override(tmp_path, monkeypatch):
    evidence_root = tmp_path / "private-evidence"
    monkeypatch.setenv("ADWATCH_EVIDENCE_ROOT", str(evidence_root))

    assert Settings.from_env().evidence_dir == evidence_root


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


def test_tiktok_collection_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(
        "ADWATCH_TIKTOK_COLLECTION_ENABLED", raising=False
    )

    settings = Settings.from_env()

    assert settings.tiktok_collection_enabled is False
    assert settings.enabled_ziniao_platforms == ("shopee",)


def test_disabled_tiktok_only_requires_shopee_store_id(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADWATCH_TIKTOK_COLLECTION_ENABLED", "false")
    monkeypatch.delenv("ZINIAO_TIKTOK_STORE_ID", raising=False)
    monkeypatch.setenv("ZINIAO_SHOPEE_STORE_ID", "222")

    settings = Settings.from_env()

    assert settings.tiktok_collection_enabled is False
    assert settings.enabled_ziniao_platforms == ("shopee",)
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


def test_feishu_callback_configuration(monkeypatch):
    monkeypatch.setenv("FEISHU_CALLBACK_SECRET", "secret")
    monkeypatch.setenv("FEISHU_CALLBACK_PUBLIC_URL", "https://example.test/cb")

    settings = Settings.from_env()

    assert settings.feishu_callback_secret == "secret"
    assert settings.feishu_callback_ready is True


def test_live_writes_default_off_and_allowlist_is_explicit(monkeypatch):
    monkeypatch.setenv("ADWATCH_LIVE_WRITES", "true")
    monkeypatch.setenv(
        "ADWATCH_LIVE_ALLOWLIST",
        "shopee:store-1:campaign-1,tiktok:store-2:campaign-2",
    )

    settings = Settings.from_env()

    assert settings.live_writes is True
    assert settings.live_allowlist == frozenset(
        {
            ("shopee", "store-1", "campaign-1"),
            ("tiktok", "store-2", "campaign-2"),
        }
    )


def test_schedule_settings_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("ADWATCH_DAILY_RUN_TIME", "09:00")
    monkeypatch.setenv("ADWATCH_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("ADWATCH_BRIDGE_RETRIES", "4")
    monkeypatch.setenv("ADWATCH_BRIDGE_RETRY_DELAY_SECONDS", "2")
    monkeypatch.setenv("ADWATCH_EXCHANGE_RATE_MAX_STALENESS_DAYS", "6")
    monkeypatch.setenv("ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS", "4")

    settings = Settings.from_env()

    assert settings.daily_run_hour == 9
    assert settings.daily_run_minute == 0
    assert settings.timezone == "Asia/Shanghai"
    assert settings.bridge_retries == 4
    assert settings.bridge_retry_delay_seconds == 2
    assert settings.exchange_rate_max_staleness_days == 6
    assert settings.shopee_history_lookback_days == 4


def test_shopee_history_lookback_defaults_to_three(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(
        "ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS",
        raising=False,
    )

    assert Settings.from_env().shopee_history_lookback_days == 3


def test_shopee_history_lookback_rejects_zero(monkeypatch):
    monkeypatch.setenv("ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS", "0")

    try:
        Settings.from_env()
    except ValueError as error:
        assert "ADWATCH_SHOPEE_HISTORY_LOOKBACK_DAYS" in str(error)
    else:
        raise AssertionError("expected invalid lookback to fail")


def test_income_scan_root_supports_environment_override(tmp_path, monkeypatch):
    scan_root = tmp_path / "income-downloads"
    monkeypatch.setenv("ADWATCH_INCOME_SCAN_ROOT", str(scan_root))

    assert Settings.from_env().income_scan_root == scan_root


def test_schedule_settings_reject_invalid_timezone(monkeypatch):
    monkeypatch.setenv("ADWATCH_TIMEZONE", "Mars/Olympus")

    try:
        Settings.from_env()
    except ValueError as error:
        assert "ADWATCH_TIMEZONE" in str(error)
    else:
        raise AssertionError("invalid timezone must be rejected")

from decimal import Decimal

import pytest

from adwatch.config import Settings


def test_settings_use_explicit_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ADWATCH_EVIDENCE_ROOT", raising=False)
    settings = Settings.from_env()
    assert settings.database_path == tmp_path / "adwatch.sqlite3"
    assert settings.report_dir == tmp_path / "reports"


def test_official_ziniao_cli_settings_use_store_ids(monkeypatch):
    monkeypatch.setenv("ZINIAO_SHOPEE_STORE_ID", "222")

    settings = Settings.from_env()

    assert settings.ziniao_shopee_store_id == "222"


def test_settings_load_project_dotenv_without_overwriting_environment(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text(
        "ZINIAO_SHOPEE_STORE_ID=from-file\nADWATCH_HISTORY_DAYS=22\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZINIAO_SHOPEE_STORE_ID", "from-environment")
    monkeypatch.delenv("ADWATCH_HISTORY_DAYS", raising=False)

    settings = Settings.from_env()

    assert settings.ziniao_shopee_store_id == "from-environment"
    assert settings.history_days == 22


def test_settings_expose_only_shopee_optimizer_configuration(tmp_path):
    settings = Settings(data_dir=tmp_path)

    for removed in (
        "ziniao_tiktok_store_id",
        "tiktok_collection_enabled",
        "feishu_webhook",
        "live_writes",
        "income_scan_root",
        "publish_ssh_target",
    ):
        assert not hasattr(settings, removed)


def test_schedule_settings_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("ADWATCH_DAILY_RUN_TIME", "09:00")
    monkeypatch.setenv("ADWATCH_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("ADWATCH_BRIDGE_RETRIES", "4")
    monkeypatch.setenv("ADWATCH_BRIDGE_RETRY_DELAY_SECONDS", "2")
    monkeypatch.setenv("ADWATCH_PAGE_READY_RETRIES", "8")
    monkeypatch.setenv("ADWATCH_PAGE_READY_RETRY_DELAY_SECONDS", "1")

    settings = Settings.from_env()

    assert settings.daily_run_hour == 9
    assert settings.daily_run_minute == 0
    assert settings.timezone == "Asia/Shanghai"
    assert settings.bridge_retries == 4
    assert settings.bridge_retry_delay_seconds == 2
    assert settings.page_ready_retries == 8
    assert settings.page_ready_retry_delay_seconds == 1


def test_schedule_settings_reject_invalid_timezone(monkeypatch):
    monkeypatch.setenv("ADWATCH_TIMEZONE", "Mars/Olympus")

    try:
        Settings.from_env()
    except ValueError as error:
        assert "ADWATCH_TIMEZONE" in str(error)
    else:
        raise AssertionError("invalid timezone must be rejected")


def test_ziniao_cli_timeout_comes_from_positive_environment_setting(monkeypatch):
    monkeypatch.setenv("ADWATCH_ZINIAO_CLI_TIMEOUT_SECONDS", "47")

    assert Settings.from_env().ziniao_cli_timeout_seconds == 47

    monkeypatch.setenv("ADWATCH_ZINIAO_CLI_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="ADWATCH_ZINIAO_CLI_TIMEOUT_SECONDS"):
        Settings.from_env()


def test_shopee_ads_pagination_limit_comes_from_positive_environment_setting(
    monkeypatch,
):
    monkeypatch.setenv("ADWATCH_SHOPEE_ADS_MAX_PAGES", "12")

    assert Settings.from_env().shopee_ads_max_pages == 12

    monkeypatch.setenv("ADWATCH_SHOPEE_ADS_MAX_PAGES", "0")
    with pytest.raises(ValueError, match="ADWATCH_SHOPEE_ADS_MAX_PAGES"):
        Settings.from_env()


def test_optimizer_settings_use_safe_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in (
        "ADWATCH_HISTORY_DAYS",
        "ADWATCH_NO_DATA_RECHECK_DAYS",
        "ADWATCH_BACKFILL_CLAIM_MINUTES",
        "ADWATCH_WEB_HOST",
        "ADWATCH_WEB_PORT",
        "ADWATCH_WEB_NONCE_TTL_SECONDS",
        "ADWATCH_RECONCILIATION_TOLERANCE",
        "ADWATCH_SHORT_WINDOW_DAYS",
        "ADWATCH_LONG_WINDOW_DAYS",
        "ADWATCH_DEFAULT_BUDGET_STEP",
        "ADWATCH_LOW_CONFIDENCE_BUDGET_STEP",
        "ADWATCH_MAX_BUDGET_STEP",
        "ADWATCH_MAX_TARGET_ROAS_STEP",
        "ADWATCH_MIN_ORDERS",
        "ADWATCH_COOLDOWN_DAYS",
        "ADWATCH_EVALUATION_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.history_days == 30
    assert settings.no_data_recheck_days == 7
    assert settings.web_host == "127.0.0.1"
    assert settings.web_port == 8765
    assert settings.web_nonce_ttl_seconds == 1800
    assert settings.reconciliation_tolerance == Decimal("0.01")
    assert settings.short_window_days == 7
    assert settings.long_window_days == 30
    assert settings.default_budget_step == Decimal("0.10")
    assert settings.low_confidence_budget_step == Decimal("0.05")
    assert settings.max_budget_step == Decimal("0.20")
    assert settings.max_target_roas_step == Decimal("0.20")
    assert settings.min_orders == 3
    assert settings.cooldown_days == 3
    assert settings.evaluation_days == 7


def test_web_nonce_ttl_comes_from_positive_environment_setting(monkeypatch):
    monkeypatch.setenv("ADWATCH_WEB_NONCE_TTL_SECONDS", "90")

    assert Settings.from_env().web_nonce_ttl_seconds == 90

    monkeypatch.setenv("ADWATCH_WEB_NONCE_TTL_SECONDS", "0")
    with pytest.raises(ValueError, match="ADWATCH_WEB_NONCE_TTL_SECONDS"):
        Settings.from_env()


def test_policy_controls_load_from_environment(monkeypatch):
    monkeypatch.setenv("ADWATCH_MIN_HISTORY_DAYS", "4")
    monkeypatch.setenv("ADWATCH_MIN_BUDGET_UTILIZATION", "0.75")
    monkeypatch.setenv("ADWATCH_FLOOR_GUARD_BAND", "0.08")
    monkeypatch.setenv("ADWATCH_SEVERE_UNDERPERFORMANCE_RATIO", "0.60")
    monkeypatch.setenv("ADWATCH_STRUCTURE_WINNER_SHARE", "0.70")
    monkeypatch.setenv("ADWATCH_STRUCTURE_MIN_ITEMS", "3")
    monkeypatch.setenv("ADWATCH_EFFECTIVENESS_MIN_SAMPLES", "8")
    monkeypatch.setenv("ADWATCH_DEFAULT_GROWTH_INTENSITY", "0.60")
    monkeypatch.setenv("ADWATCH_ROLLBACK_DAYS", "4")
    monkeypatch.setenv("ADWATCH_MINIMUM_GMV_GROWTH", "0.06")

    settings = Settings.from_env()

    assert settings.min_history_days == 4
    assert settings.min_budget_utilization == Decimal("0.75")
    assert settings.floor_guard_band == Decimal("0.08")
    assert settings.severe_underperformance_ratio == Decimal("0.60")
    assert settings.structure_winner_share == Decimal("0.70")
    assert settings.structure_min_items == 3
    assert settings.effectiveness_min_samples == 8
    assert settings.default_growth_intensity == Decimal("0.60")
    assert settings.rollback_days == 4
    assert settings.minimum_gmv_growth == Decimal("0.06")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ADWATCH_MIN_BUDGET_UTILIZATION", "NaN"),
        ("ADWATCH_FLOOR_GUARD_BAND", "1.01"),
        ("ADWATCH_SEVERE_UNDERPERFORMANCE_RATIO", "-0.01"),
        ("ADWATCH_STRUCTURE_WINNER_SHARE", "Infinity"),
        ("ADWATCH_DEFAULT_GROWTH_INTENSITY", "NaN"),
        ("ADWATCH_MINIMUM_GMV_GROWTH", "1.01"),
        ("ADWATCH_MIN_HISTORY_DAYS", "0"),
        ("ADWATCH_STRUCTURE_MIN_ITEMS", "0"),
    ],
)
def test_policy_controls_reject_invalid_environment_values(name, value, monkeypatch):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        Settings.from_env()


def test_optimizer_no_data_recheck_days_uses_positive_environment_setting(monkeypatch):
    monkeypatch.setenv("ADWATCH_NO_DATA_RECHECK_DAYS", "9")
    assert Settings.from_env().no_data_recheck_days == 9

    monkeypatch.setenv("ADWATCH_NO_DATA_RECHECK_DAYS", "0")
    with pytest.raises(ValueError, match="ADWATCH_NO_DATA_RECHECK_DAYS"):
        Settings.from_env()


def test_backfill_claim_minutes_uses_positive_environment_setting(monkeypatch):
    monkeypatch.setenv("ADWATCH_BACKFILL_CLAIM_MINUTES", "11")
    assert Settings.from_env().backfill_claim_minutes == 11

    monkeypatch.setenv("ADWATCH_BACKFILL_CLAIM_MINUTES", "0")
    with pytest.raises(ValueError, match="ADWATCH_BACKFILL_CLAIM_MINUTES"):
        Settings.from_env()


def test_optimizer_settings_load_from_environment(monkeypatch):
    monkeypatch.setenv("ADWATCH_HISTORY_DAYS", "45")
    monkeypatch.setenv("ADWATCH_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("ADWATCH_WEB_PORT", "9000")
    monkeypatch.setenv("ADWATCH_RECONCILIATION_TOLERANCE", "0.05")
    monkeypatch.setenv("ADWATCH_SHORT_WINDOW_DAYS", "5")
    monkeypatch.setenv("ADWATCH_LONG_WINDOW_DAYS", "20")
    monkeypatch.setenv("ADWATCH_DEFAULT_BUDGET_STEP", "0.08")
    monkeypatch.setenv("ADWATCH_LOW_CONFIDENCE_BUDGET_STEP", "0.04")
    monkeypatch.setenv("ADWATCH_MAX_BUDGET_STEP", "0.16")
    monkeypatch.setenv("ADWATCH_MAX_TARGET_ROAS_STEP", "0.12")
    monkeypatch.setenv("ADWATCH_MIN_ORDERS", "5")
    monkeypatch.setenv("ADWATCH_COOLDOWN_DAYS", "2")
    monkeypatch.setenv("ADWATCH_EVALUATION_DAYS", "10")

    settings = Settings.from_env()

    assert settings.history_days == 45
    assert settings.web_host == "127.0.0.1"
    assert settings.web_port == 9000
    assert settings.reconciliation_tolerance == Decimal("0.05")
    assert settings.short_window_days == 5
    assert settings.long_window_days == 20
    assert settings.default_budget_step == Decimal("0.08")
    assert settings.low_confidence_budget_step == Decimal("0.04")
    assert settings.max_budget_step == Decimal("0.16")
    assert settings.max_target_roas_step == Decimal("0.12")
    assert settings.min_orders == 5
    assert settings.cooldown_days == 2
    assert settings.evaluation_days == 10


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ADWATCH_WEB_HOST", "0.0.0.0"),
        ("ADWATCH_WEB_PORT", "65536"),
        ("ADWATCH_RECONCILIATION_TOLERANCE", "-0.01"),
        ("ADWATCH_DEFAULT_BUDGET_STEP", "1.01"),
        ("ADWATCH_LOW_CONFIDENCE_BUDGET_STEP", "0.21"),
        ("ADWATCH_MIN_ORDERS", "0"),
        ("ADWATCH_EVALUATION_DAYS", "0"),
    ],
)
def test_optimizer_settings_reject_invalid_values(name, value, monkeypatch):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        Settings.from_env()


def test_optimizer_settings_require_ordered_windows_and_budget_steps(monkeypatch):
    monkeypatch.setenv("ADWATCH_SHORT_WINDOW_DAYS", "31")
    monkeypatch.setenv("ADWATCH_LONG_WINDOW_DAYS", "30")

    with pytest.raises(ValueError, match="ADWATCH_LONG_WINDOW_DAYS"):
        Settings.from_env()

    monkeypatch.setenv("ADWATCH_SHORT_WINDOW_DAYS", "7")
    monkeypatch.setenv("ADWATCH_LOW_CONFIDENCE_BUDGET_STEP", "0.11")
    monkeypatch.setenv("ADWATCH_DEFAULT_BUDGET_STEP", "0.10")

    with pytest.raises(ValueError, match="ADWATCH_LOW_CONFIDENCE_BUDGET_STEP"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_optimizer_settings_reject_non_finite_decimal_values(value, monkeypatch):
    monkeypatch.setenv("ADWATCH_DEFAULT_BUDGET_STEP", value)

    with pytest.raises(ValueError, match="ADWATCH_DEFAULT_BUDGET_STEP.*finite"):
        Settings.from_env()


@pytest.mark.parametrize(
    "field",
    [
        "bridge_retries",
        "bridge_retry_delay_seconds",
        "page_ready_retries",
        "page_ready_retry_delay_seconds",
        "ziniao_cli_timeout_seconds",
        "shopee_ads_max_pages",
        "history_days",
        "no_data_recheck_days",
        "backfill_claim_minutes",
        "short_window_days",
        "long_window_days",
        "min_orders",
        "evaluation_days",
    ],
)
def test_settings_post_init_rejects_non_positive_integer_controls(tmp_path, field):
    with pytest.raises(ValueError, match=field):
        Settings(data_dir=tmp_path, **{field: 0})


def test_settings_post_init_rejects_invalid_direct_constructor_ranges(tmp_path):
    with pytest.raises(ValueError, match="cooldown_days"):
        Settings(data_dir=tmp_path, cooldown_days=-1)
    with pytest.raises(ValueError, match="web_port"):
        Settings(data_dir=tmp_path, web_port=65536)
    with pytest.raises(ValueError, match="reconciliation_tolerance"):
        Settings(data_dir=tmp_path, reconciliation_tolerance=Decimal("NaN"))
    with pytest.raises(ValueError, match="default_budget_step"):
        Settings(data_dir=tmp_path, default_budget_step=Decimal("1.1"))
    with pytest.raises(ValueError, match="long_window_days"):
        Settings(data_dir=tmp_path, short_window_days=10, long_window_days=9)


def test_optimizer_web_body_limit_is_a_strict_positive_setting(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADWATCH_WEB_MAX_BODY_BYTES", "2048")
    assert Settings.from_env().web_max_body_bytes == 2048

    monkeypatch.setenv("ADWATCH_WEB_MAX_BODY_BYTES", "0")
    with pytest.raises(ValueError, match="ADWATCH_WEB_MAX_BODY_BYTES"):
        Settings.from_env()

    with pytest.raises(ValueError, match="web_max_body_bytes"):
        Settings(data_dir=tmp_path, web_max_body_bytes=True)


def test_settings_post_init_rejects_unordered_direct_budget_steps(tmp_path):
    with pytest.raises(ValueError, match="low_confidence_budget_step"):
        Settings(
            data_dir=tmp_path,
            low_confidence_budget_step=Decimal("0.11"),
            default_budget_step=Decimal("0.10"),
        )


@pytest.mark.parametrize("daily_run_time", ["9:00", "24:00", "12:60"])
def test_schedule_settings_reject_invalid_daily_run_time(daily_run_time, monkeypatch):
    monkeypatch.setenv("ADWATCH_DAILY_RUN_TIME", daily_run_time)

    with pytest.raises(ValueError, match="ADWATCH_DAILY_RUN_TIME"):
        Settings.from_env()

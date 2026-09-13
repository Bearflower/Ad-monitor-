from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import adwatch.cli
from adwatch.cli import build_parser, main
from adwatch.config import Settings
from adwatch.optimizer.daily import DailyRunResult
from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.evaluator import EvaluatorService
from adwatch.optimizer.models import (
    AdMetric,
    DataQuality,
    DataQualityStatus,
    MetricGrain,
)
from adwatch.optimizer.policy import Optimizer
from adwatch.optimizer.repository import OptimizerRepository


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def initialize(self, report_date: date) -> DailyRunResult:
        self.calls.append(("init", report_date))
        return DailyRunResult("success", report_date, Path("init.md"))

    def collect(self, data_date: date) -> str:
        self.calls.append(("collect", data_date))
        return "success"

    def run_daily(self, report_date: date) -> DailyRunResult:
        self.calls.append(("daily", report_date))
        return DailyRunResult("success", report_date, Path("daily.md"))

    def dashboard(self, port: int) -> None:
        self.calls.append(("dashboard", "127.0.0.1", port))

    def schedule(self, action: str) -> str:
        self.calls.append(("schedule", action))
        return "healthy"

    def doctor(self, port: int) -> tuple[tuple[str, bool], ...]:
        self.calls.append(("doctor", port))
        return (
            ("runtime", True),
            ("database", True),
            ("ziniao_cli", True),
            ("shopee_store", True),
            ("local_port", True),
        )


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Runtime:
    value = _Runtime()
    settings = Settings(data_dir=tmp_path, ziniao_shopee_store_id="store-1")
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "_build_optimizer_runtime", lambda _: value)
    monkeypatch.setattr(adwatch.cli, "_business_today", lambda _: date(2026, 9, 11))
    return value


def test_parser_exposes_only_six_optimizer_commands() -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if action.dest == "command"
    )

    assert set(subparsers.choices) == {
        "init", "collect", "run", "dashboard", "schedule", "doctor"
    }


def test_collect_is_shopee_only_and_rejects_legacy_mode(runtime: _Runtime) -> None:
    assert main(["collect", "--date", "2026-09-10"]) == 0
    assert runtime.calls == [("collect", date(2026, 9, 10))]

    assert main(["collect", "--mode", "mock"]) == 2
    assert runtime.calls == [("collect", date(2026, 9, 10))]


def test_init_backfills_through_today_and_immediately_reports(
    runtime: _Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init"]) == 0
    assert runtime.calls == [("init", date(2026, 9, 11))]
    output = capsys.readouterr().out
    assert "status=success" in output
    assert "report=init.md" in output


def test_run_daily_defaults_to_shanghai_business_today(runtime: _Runtime) -> None:
    assert main(["run", "daily"]) == 0
    assert runtime.calls == [("daily", date(2026, 9, 11))]


def test_run_rejects_non_daily_workflow(runtime: _Runtime) -> None:
    assert main(["run", "scheduled-daily"]) == 2
    assert runtime.calls == []


def test_dashboard_has_no_remote_bind_option(runtime: _Runtime) -> None:
    assert main(["dashboard", "--port", "9000"]) == 0
    assert runtime.calls == [("dashboard", "127.0.0.1", 9000)]
    assert main(["dashboard", "--host", "0.0.0.0"]) == 2


@pytest.mark.parametrize("action", ["install", "status", "uninstall"])
def test_schedule_exposes_only_three_actions(runtime: _Runtime, action: str) -> None:
    assert main(["schedule", action]) == 0
    assert runtime.calls == [("schedule", action)]


def test_doctor_reports_only_five_local_checks(
    runtime: _Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "runtime=ok",
        "database=ok",
        "ziniao_cli=ok",
        "shopee_store=ok",
        "local_port=ok",
    ]
    assert runtime.calls == [("doctor", 8765)]


def test_doctor_returns_failure_if_a_required_check_fails(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime,
        "doctor",
        lambda port: (("runtime", True), ("database", False)),
    )

    assert main(["doctor"]) == 2


def test_help_does_not_construct_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adwatch.cli,
        "_build_optimizer_runtime",
        lambda _: pytest.fail("help must not construct runtime"),
        raising=False,
    )
    assert main(["--help"]) == 0


def test_doctor_runtime_can_be_built_when_store_configuration_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.shutil, "which", lambda _: None)

    runtime = adwatch.cli._build_optimizer_runtime(settings)

    checks = dict(runtime.doctor(8765))
    assert checks["shopee_store"] is False


def test_doctor_converts_database_errors_to_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = adwatch.cli._build_optimizer_runtime(Settings(data_dir=tmp_path))

    class BrokenDatabase:
        def migrate(self) -> None:
            raise sqlite3.OperationalError("secret path")

    runtime.database = BrokenDatabase()  # type: ignore[assignment]
    monkeypatch.setattr(adwatch.cli.shutil, "which", lambda _: None)

    assert dict(runtime.doctor(8765))["database"] is False


def test_doctor_rejects_quick_check_database_without_optimizer_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = adwatch.cli._build_optimizer_runtime(Settings(data_dir=tmp_path))
    empty_path = tmp_path / "empty.sqlite3"

    class MissingSchemaDatabase:
        def migrate(self) -> None:
            return None

        def connect(self) -> sqlite3.Connection:
            return sqlite3.connect(empty_path)

    runtime.database = MissingSchemaDatabase()  # type: ignore[assignment]
    monkeypatch.setattr(adwatch.cli.shutil, "which", lambda _: None)

    assert dict(runtime.doctor(8765))["database"] is False


def test_real_workflow_passes_recent_start_to_campaign_read_model(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, history_days=30)
    database = OptimizerDatabase(settings.database_path)
    database.migrate()
    repository = OptimizerRepository(database)
    workflow = adwatch.cli._WorkflowStages(
        repository,
        Optimizer(repository, settings),
        EvaluatorService(repository, settings),
        recent_days=settings.history_days,
    )

    assert workflow.ensure_profiles(
        "store-1", date(2026, 9, 10), datetime(2026, 9, 11, tzinfo=UTC)
    ) == ()


def test_real_workflow_propagates_placeholder_campaign_name_quality(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, history_days=30)
    database = OptimizerDatabase(settings.database_path)
    database.migrate()
    repository = OptimizerRepository(database)
    observed_at = datetime(2026, 9, 11, tzinfo=UTC)
    data_date = date(2026, 9, 10)
    repository.upsert_metric(
        AdMetric(
            "store-1",
            "campaign-1",
            "...",
            "__ALL__",
            data_date,
            "THB",
            Decimal(10),
            Decimal(30),
            3,
            observed_at,
            "test",
            MetricGrain.CAMPAIGN,
        )
    )
    repository.record_collection_day(
        "store-1", data_date, "success", attempted_at=observed_at
    )
    workflow = adwatch.cli._WorkflowStages(
        repository,
        Optimizer(repository, settings),
        EvaluatorService(repository, settings),
        recent_days=settings.history_days,
    )

    quality = workflow.assess("store-1", data_date, observed_at)

    assert quality.status is DataQualityStatus.DEGRADED
    assert quality.allow_aggressive_scale is False
    assert quality.missing_fields == ("campaign_name",)


def test_second_campaign_planning_failure_leaves_no_partial_recommendations(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, history_days=30)
    database = OptimizerDatabase(settings.database_path)
    database.migrate()
    repository = OptimizerRepository(database)
    observed_at = datetime(2026, 9, 11, tzinfo=UTC)
    for campaign_id in ("campaign-a", "campaign-b"):
        repository.upsert_metric(
            AdMetric(
                "store-1", campaign_id, campaign_id, "__ALL__",
                date(2026, 9, 10), "THB", Decimal(10),
                Decimal(30), 3, observed_at, "test",
                MetricGrain.CAMPAIGN,
            )
        )
    actual = Optimizer(repository, settings)

    class FailSecond:
        def plan(self, store_id, campaign_id, data_date, quality, decision_at):
            if campaign_id == "campaign-b":
                raise RuntimeError("second failed")
            return actual.plan(store_id, campaign_id, data_date, quality, decision_at)

        def recommend(self, store_id, campaign_id, data_date, quality, decision_at):
            if campaign_id == "campaign-b":
                raise RuntimeError("second failed")
            return actual.recommend(
                store_id, campaign_id, data_date, quality, decision_at
            )

    workflow = adwatch.cli._WorkflowStages(
        repository,
        FailSecond(),
        EvaluatorService(repository, settings),
        recent_days=settings.history_days,
    )

    with pytest.raises(RuntimeError, match="second failed"):
        workflow.recommend(
            "store-1",
            date(2026, 9, 10),
            DataQuality(DataQualityStatus.STALE, observed_at),
            observed_at,
        )

    assert repository.list_recommendations(
        "store-1", "campaign-a", date(2026, 9, 10)
    ) == ()


def test_cli_has_no_runtime_dependency_on_legacy_scheduler() -> None:
    assert "adwatch.operations.scheduler" not in inspect.getsource(adwatch.cli)


def test_runtime_schedule_uses_new_optimizer_schedule_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class Status:
        installed = False
        healthy = False

    class Manager:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

        def status(self) -> Status:
            return Status()

    other_directory = tmp_path / "other"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)
    monkeypatch.setattr(adwatch.cli, "ScheduleManager", Manager)
    runtime = adwatch.cli._build_optimizer_runtime(Settings(data_dir=tmp_path))

    assert runtime.schedule("status") == "not_installed"
    assert observed["project_dir"] == Path(adwatch.cli.__file__).resolve().parents[2]
    assert observed["settings"] == runtime.settings
    assert callable(observed["doctor"])


def test_dashboard_passes_configured_campaign_history_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}
    settings = Settings(data_dir=tmp_path, history_days=17)
    runtime = adwatch.cli._build_optimizer_runtime(settings)
    monkeypatch.setattr(
        adwatch.cli,
        "serve_optimizer_web",
        lambda repository, **kwargs: observed.update(kwargs),
    )

    runtime.dashboard(9000)

    assert observed["campaign_recent_days"] == 17

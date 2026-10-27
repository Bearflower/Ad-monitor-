"""Shopee 广告优化器的精简本机命令行入口。"""

from __future__ import annotations

import argparse
import shutil
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from adwatch.config import Settings
from adwatch.optimizer.backfill import BackfillService
from adwatch.optimizer.collector import ShopeeAdCollector
from adwatch.optimizer.daily import DailyRunResult, FileRunLock, OptimizerDailyService
from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.evaluator import EvaluatorService
from adwatch.optimizer.models import DataQuality, DataQualityStatus, MetricGrain
from adwatch.optimizer.policy import Optimizer
from adwatch.optimizer.report import ShopeeAdsReportService
from adwatch.optimizer.repository import OptimizerRepository
from adwatch.optimizer.scheduler import ScheduleCommandError, ScheduleManager
from adwatch.optimizer.web import serve_optimizer_web
from adwatch.optimizer.ziniao import (
    ShopeeSession,
    ZiniaoCliClient,
    ZiniaoCliError,
    normalize_campaign_name,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adwatch")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("init")

    collect = commands.add_parser("collect")
    collect.add_argument("--date", type=date.fromisoformat)

    run = commands.add_parser("run")
    workflows = run.add_subparsers(dest="workflow")
    daily = workflows.add_parser("daily")
    daily.add_argument("--date", type=date.fromisoformat)

    dashboard = commands.add_parser("dashboard")
    dashboard.add_argument("--port", type=_port, default=None)

    schedule = commands.add_parser("schedule")
    schedule.add_argument("schedule_command", choices=("install", "status", "uninstall"))
    commands.add_parser("doctor")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if args.command is None:
        parser.print_help()
        return 0

    settings = Settings.from_env()
    runtime = _build_optimizer_runtime(settings)
    today = _business_today(settings)
    if args.command == "init":
        return _print_run(runtime.initialize(today))
    if args.command == "collect":
        status = runtime.collect(args.date or today - timedelta(days=1))
        print(f"collect={status}")
        return 0 if status in {"success", "no_data"} else 2
    if args.command == "run":
        if args.workflow != "daily":
            parser.print_help()
            return 2
        return _print_run(runtime.run_daily(args.date or today))
    if args.command == "dashboard":
        runtime.dashboard(args.port or settings.web_port)
        return 0
    if args.command == "schedule":
        status = runtime.schedule(args.schedule_command)
        print(f"schedule={status}")
        return 0 if status in {"healthy", "installed", "uninstalled"} else 2
    if args.command == "doctor":
        checks = runtime.doctor(settings.web_port)
        for name, healthy in checks:
            print(f"{name}={'ok' if healthy else 'failed'}")
        return 0 if all(healthy for _, healthy in checks) else 2
    return 2


def entrypoint() -> None:
    raise SystemExit(main())


@dataclass
class _OptimizerRuntime:
    settings: Settings
    database: OptimizerDatabase
    repository: OptimizerRepository
    client: ZiniaoCliClient
    daily: OptimizerDailyService

    def initialize(self, report_date: date) -> DailyRunResult:
        return self.daily.initialize(report_date)

    def collect(self, data_date: date) -> str:
        self.database.migrate()
        return self.daily.collect_day(data_date)

    def run_daily(self, report_date: date) -> DailyRunResult:
        return self.daily.run_daily(report_date)

    def dashboard(self, port: int) -> None:
        self.database.migrate()
        print(f"dashboard=http://127.0.0.1:{port}")
        serve_optimizer_web(
            self.repository,
            store_id=self.settings.ziniao_shopee_store_id,
            host="127.0.0.1",
            port=port,
            max_body_bytes=self.settings.web_max_body_bytes,
            nonce_ttl_seconds=self.settings.web_nonce_ttl_seconds,
            campaign_recent_days=self.settings.history_days,
        )

    def schedule(self, action: str) -> str:
        manager = ScheduleManager(
            project_dir=_optimizer_project_dir(),
            settings=self.settings,
            doctor=lambda: self.doctor(self.settings.web_port),
        )
        try:
            if action == "install":
                return "installed" if manager.install().healthy else "failed"
            if action == "status":
                status = manager.status()
                if status.healthy:
                    return "healthy"
                return "not_installed" if not status.installed else "unhealthy"
            status = manager.uninstall()
            return "uninstalled" if not status.installed else "failed"
        except ScheduleCommandError:
            return "failed"

    def doctor(self, port: int) -> tuple[tuple[str, bool], ...]:
        runtime = Path(sys.executable).is_file()
        database = self._database_healthy()
        cli = shutil.which("ziniao-cli") is not None
        store = self._store_healthy() if cli else False
        page = self._page_healthy() if store else False
        return (
            ("runtime", runtime),
            ("database", database),
            ("ziniao_cli", cli),
            ("shopee_store", store),
            ("shopee_page", page),
            ("local_port", _local_port_available(port)),
        )

    def _database_healthy(self) -> bool:
        try:
            self.database.migrate()
            return self.database.schema_is_current()
        except (AttributeError, OSError, RuntimeError, sqlite3.Error):
            return False

    def _store_healthy(self) -> bool:
        expected = self.settings.ziniao_shopee_store_id.strip()
        if not expected:
            return False
        try:
            stores = self.client.store_list()
        except ZiniaoCliError:
            return False
        return any(
            str(item.get("storeId", "")) == expected
            and "shopee" in str(item.get("platformName", "")).lower()
            for item in stores
        )


    def _page_healthy(self) -> bool:
        try:
            page = self.client.page_exec(
                self.settings.ziniao_shopee_store_id, "session_status"
            )
        except ZiniaoCliError:
            return False
        return isinstance(page, dict) and isinstance(page.get("url"), str) and bool(page["url"])


class _WorkflowStages:
    def __init__(
        self,
        repository: OptimizerRepository,
        optimizer: Optimizer,
        evaluator: EvaluatorService,
        *,
        recent_days: int,
    ) -> None:
        if recent_days < 1:
            raise ValueError("recent_days must be positive")
        self.repository = repository
        self.optimizer = optimizer
        self.evaluator = evaluator
        self.recent_days = recent_days

    def _recent_start(self, data_date: date) -> date:
        return data_date - timedelta(days=self.recent_days - 1)

    def ensure_profiles(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> tuple[object, ...]:
        campaigns = self.repository.campaign_read_models_as_of(
            store_id,
            data_through=data_date,
            decision_at=decision_at,
            recent_start=self._recent_start(data_date),
        )
        return tuple(
            self.optimizer.ensure_profile(
                store_id, item.campaign_id, data_date, decision_at
            )
            for item in campaigns
            if item.snapshot is not None or item.metric is not None
        )

    def assess(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> DataQuality:
        snapshot = self.repository.report_snapshot_as_of(
            store_id, data_date, decision_at
        )
        current = (
            snapshot.collection is not None
            and snapshot.collection.status == "success"
            and snapshot.actual_data_date == data_date
            and bool(snapshot.metrics)
        )
        missing_fields: list[str] = []
        notes: list[str] = []
        if not current:
            missing_fields.append("current_campaign_metrics")
            notes.append("当前报告日没有完整活动指标。")
        if any(
            metric.grain is MetricGrain.CAMPAIGN
            and not normalize_campaign_name(metric.campaign_name)
            for metric in snapshot.metrics
        ):
            missing_fields.append("campaign_name")
            notes.append("部分活动名称未从 Shopee 页面完整获取。")
        status = (
            DataQualityStatus.STALE
            if not current
            else DataQualityStatus.DEGRADED
            if missing_fields
            else DataQualityStatus.HEALTHY
        )
        return DataQuality(
            status,
            decision_at,
            allow_aggressive_scale=status is DataQualityStatus.HEALTHY,
            missing_fields=tuple(missing_fields),
            note=" ".join(notes),
        )

    def evaluate_pending(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> tuple[object, ...]:
        return tuple(
            self.evaluator.evaluate_feedback(
                store_id, item.feedback_id, data_date, decision_at
            )
            for item in self.repository.pending_feedback_for_store(
                store_id, as_of=decision_at
            )
        )

    def recommend(
        self,
        store_id: str,
        data_date: date,
        quality: DataQuality,
        decision_at: datetime,
    ) -> tuple[object, ...]:
        campaigns = self.repository.campaign_read_models_as_of(
            store_id,
            data_through=data_date,
            decision_at=decision_at,
            recent_start=self._recent_start(data_date),
        )
        planned = tuple(
            recommendation
            for item in campaigns
            if item.metric is not None and item.metric.data_date == data_date
            for recommendation in self.optimizer.plan(
                store_id, item.campaign_id, data_date, quality, decision_at
            )
        )
        return self.repository.save_recommendations(planned)


def _build_optimizer_runtime(settings: Settings) -> _OptimizerRuntime:
    database = OptimizerDatabase(settings.database_path)
    repository = OptimizerRepository(database)
    client = ZiniaoCliClient(settings=settings)
    collector = ShopeeAdCollector(settings, client, repository=repository)
    optimizer = Optimizer(
        repository,
        settings,
        effectiveness_provider=EvaluatorService(repository, settings),
    )
    evaluator = EvaluatorService(repository, settings)
    workflow = _WorkflowStages(
        repository,
        optimizer,
        evaluator,
        recent_days=settings.history_days,
    )
    daily = OptimizerDailyService(
        settings.ziniao_shopee_store_id or "__missing_shopee_store__",
        repository=repository,
        migrator=database,
        session=ShopeeSession(settings, client),
        backfill=BackfillService.from_settings(settings, repository, collector),
        collector=collector,
        profiles=workflow,
        quality=workflow,
        evaluator=workflow,
        optimizer=workflow,
        reporter=ShopeeAdsReportService(repository, settings.report_dir),
        run_lock=FileRunLock(settings.data_dir / "optimizer.lock"),
    )
    return _OptimizerRuntime(settings, database, repository, client, daily)


def _print_run(result: DailyRunResult) -> int:
    parts = [f"status={result.status}", f"report_date={result.report_date.isoformat()}"]
    if result.report_path is not None:
        parts.append(f"report={result.report_path}")
    if result.skipped:
        parts.append("skipped=true")
    print(" ".join(parts))
    return 0 if result.status == "success" else 2


def _business_today(settings: Settings) -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(settings.timezone)).date()


def _optimizer_project_dir() -> Path:
    executable = Path(sys.argv[0]).resolve()
    if (
        executable.name == "adwatch"
        and executable.parent.name == "bin"
        and executable.parent.parent.name == ".venv"
    ):
        return executable.parents[2]
    package_project = Path(__file__).resolve().parents[2]
    if (package_project / "pyproject.toml").is_file():
        return package_project
    raise ScheduleCommandError("optimizer project directory is unavailable")


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _local_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False

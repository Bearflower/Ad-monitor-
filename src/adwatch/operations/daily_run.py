from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.collectors.registry import CollectorRegistry
from adwatch.collectors.ziniao_client import (
    ZiniaoApiError,
    ZiniaoCliClient,
)
from adwatch.config import Settings
from adwatch.domain import Platform
from adwatch.income.service import IncomeImportService, ScanResult
from adwatch.integrations.exchange_rates import (
    EcbExchangeRateSource,
    ExchangeRateResolution,
    resolve_scheduled_exchange_rate,
)
from adwatch.knowledge.repository import KnowledgeRepository
from adwatch.knowledge.service import KnowledgeService
from adwatch.knowledge.sources import DEFAULT_SOURCES
from adwatch.operations.run_state import Incident, RunStateStore
from adwatch.orders.repository import OrderRepository
from adwatch.orders.sync import OperationsSyncService
from adwatch.orders.ziniao_sync import (
    OrderSyncResult,
    ShopeeOrderSync,
    ZiniaoShopeeReadClient,
)
from adwatch.pipeline.runner import PipelineRunner, PipelineSummary
from adwatch.reporting.delivery import deliver_incident, deliver_report
from adwatch.reporting.markdown import present_daily_report
from adwatch.reporting.quality import write_quality_report
from adwatch.reporting.read_model import ReportReadModel
from adwatch.storage.db import Database


class BridgeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CollectionFailure:
    platform: str
    error_type: str
    message: str


@dataclass(frozen=True)
class CollectionOutcome:
    summaries: tuple[PipelineSummary, ...]
    failures: tuple[CollectionFailure, ...]


@dataclass(frozen=True)
class AnalysisOutcome:
    report_path: Path
    delivery_status: str
    delivery_error: str | None = None
    metrics_processed: int = 0
    recommendations: int = 0
    orders_shipped: int = 0
    orders_supplier_costed: int = 0
    orders_pending_cost: int = 0
    orders_pending_fulfillment: int = 0
    orders_pending_order_identity: int = 0


@dataclass(frozen=True)
class DailyRunResult:
    exit_code: int
    status: str
    report_path: Path | None
    collection_errors: tuple[str, ...]
    delivery_status: str = "not_attempted"
    metrics_processed: int = 0
    recommendations: int = 0
    orders_shipped: int = 0
    orders_supplier_costed: int = 0
    orders_pending_cost: int = 0
    orders_pending_fulfillment: int = 0
    orders_pending_order_identity: int = 0
    messages: tuple[str, ...] = ()

    def summary_line(self) -> str:
        report = str(self.report_path) if self.report_path else "none"
        return (
            f"daily_run={self.status} "
            f"metrics={self.metrics_processed} "
            f"recommendations={self.recommendations} "
            f"orders_shipped={self.orders_shipped} "
            f"orders_supplier_costed={self.orders_supplier_costed} "
            f"orders_pending_cost={self.orders_pending_cost} "
            f"orders_pending_fulfillment={self.orders_pending_fulfillment} "
            f"orders_pending_order_identity="
            f"{self.orders_pending_order_identity} "
            f"delivery={self.delivery_status} report={report}"
        )


class DailyRunDependencies(Protocol):
    def prepare(self) -> None: ...

    def resolve_exchange_rate(
        self,
        execution_date: date,
        report_date: date,
        mode: str,
    ) -> ExchangeRateResolution | None: ...

    def check_bridge(self, mode: str) -> None: ...

    def scan_income(self, mode: str) -> ScanResult | None: ...

    def sync_orders(
        self,
        report_date: date,
        mode: str,
    ) -> OrderSyncResult | None: ...

    def collect(self, report_date: date, mode: str) -> CollectionOutcome: ...

    def analyze_and_report(
        self,
        report_date: date,
        mode: str,
        notify: bool,
    ) -> AnalysisOutcome: ...

    def update_knowledge(self, mode: str) -> None: ...

    def handle_incident(self, incident: Incident, notify: bool) -> None: ...


class ProductionDailyRunDependencies:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.database = database
        self.sleeper = sleeper
        self.state = RunStateStore(settings.data_dir)
        self.collector_registry = CollectorRegistry(database, settings)

    def prepare(self) -> None:
        self.database.migrate()

    def resolve_exchange_rate(
        self,
        execution_date: date,
        report_date: date,
        mode: str,
    ) -> ExchangeRateResolution | None:
        if mode == "mock":
            return None
        return resolve_scheduled_exchange_rate(
            self.database,
            EcbExchangeRateSource(),
            currency="THB",
            execution_date=execution_date,
            report_date=report_date,
            max_staleness_days=(
                self.settings.exchange_rate_max_staleness_days
            ),
            resolved_at=datetime.now(
                ZoneInfo(self.settings.timezone)
            ),
        )

    def check_bridge(self, mode: str) -> None:
        if mode == "mock":
            return
        if not self.settings.ziniao_cli_ready:
            raise BridgeUnavailable(
                "ZINIAO_TIKTOK_STORE_ID 和 "
                "ZINIAO_SHOPEE_STORE_ID 未配置"
            )
        client = ZiniaoCliClient()
        last_error: Exception | None = None
        for attempt in range(self.settings.bridge_retries):
            try:
                client.get_store_list()
                return
            except (OSError, ZiniaoApiError) as error:
                last_error = error
                if attempt + 1 < self.settings.bridge_retries:
                    self.sleeper(
                        self.settings.bridge_retry_delay_seconds
                    )
        detail = (
            f"{type(last_error).__name__}: {last_error}"
            if last_error
            else "unknown error"
        )
        raise BridgeUnavailable(f"无法连接紫鸟 Bridge（{detail}）")

    def scan_income(self, mode: str) -> ScanResult | None:
        if mode == "mock":
            return None
        if self.settings.income_scan_root is None:
            return ScanResult()
        return IncomeImportService(
            self.database,
            scan_root=self.settings.income_scan_root,
        ).scan()

    def collect(self, report_date: date, mode: str) -> CollectionOutcome:
        collector_type = MockCollector if mode == "mock" else None
        runner = PipelineRunner(self.database)
        summaries: list[PipelineSummary] = []
        failures: list[CollectionFailure] = []
        platforms = (
            (Platform.TIKTOK, Platform.SHOPEE)
            if mode == "mock"
            else tuple(
                Platform(value)
                for value in self.settings.enabled_ziniao_platforms
            )
        )
        for platform in platforms:
            try:
                if mode == "mock":
                    collector = collector_type(platform)  # type: ignore[union-attr]
                else:
                    # 通过 CollectorRegistry 获取采集器（自动故障切换）
                    collector = self.collector_registry.get_collector(
                        platform.value
                    )
                summaries.append(runner.run(collector, report_date))
                if mode != "mock":
                    self.collector_registry.report_success(
                        platform.value
                    )
            except Exception as error:  # noqa: BLE001 - 每个平台独立隔离
                if mode != "mock":
                    self.collector_registry.report_failure(
                        platform.value
                    )
                failures.append(
                    CollectionFailure(
                        platform=platform.value,
                        error_type=type(error).__name__,
                        message=str(error),
                    )
                )
        write_quality_report(
            self.settings.report_dir,
            report_date,
            mode,
            summaries,
        )
        return CollectionOutcome(tuple(summaries), tuple(failures))

    def sync_orders(
        self,
        report_date: date,
        mode: str,
    ) -> OrderSyncResult | None:
        if mode == "mock":
            return None
        reader = ZiniaoShopeeReadClient(
            ZiniaoCliClient(),
            self.settings.ziniao_shopee_store_id,
        )
        return ShopeeOrderSync(
            OrderRepository(self.database),
            reader,
            store=self.settings.ziniao_shopee_store_name,
            timezone=self.settings.timezone,
        ).sync(
            start=(
                report_date
                - timedelta(
                    days=self.settings.shopee_history_lookback_days
                )
            ),
            end=report_date,
        )

    def analyze_and_report(
        self,
        report_date: date,
        mode: str,
        notify: bool,
    ) -> AnalysisOutcome:
        order_sync = OperationsSyncService(self.database).sync()
        analysis = AnalysisService(self.database)
        if mode == "mock":
            analysis.seed_mock_business_data(report_date)
        summary = analysis.run(report_date)
        presentation = present_daily_report(
            ReportReadModel(self.database).daily(report_date),
            simulated=mode == "mock",
        )
        delivery = deliver_report(
            presentation.markdown,
            data_date=report_date,
            report_dir=self.settings.report_dir,
            webhook_url=self.settings.feishu_webhook if notify else "",
            header_template=presentation.header_template,
            risk_label=presentation.risk_label,
        )
        return AnalysisOutcome(
            report_path=delivery.path,
            delivery_status=delivery.status if notify else "suppressed",
            delivery_error=(
                delivery.error
                if (
                    notify
                    and bool(self.settings.feishu_webhook)
                    and delivery.status != "sent"
                )
                else None
            ),
            metrics_processed=summary.metrics_processed,
            recommendations=summary.recommendations,
            orders_shipped=order_sync.shipped,
            orders_supplier_costed=order_sync.supplier_costed,
            orders_pending_cost=order_sync.pending_cost,
            orders_pending_fulfillment=order_sync.pending_fulfillment,
            orders_pending_order_identity=(
                order_sync.pending_order_identity
            ),
        )

    def update_knowledge(self, mode: str) -> None:
        if mode != "ziniao":
            return
        KnowledgeService(
            KnowledgeRepository(self.database),
            ZiniaoCliClient(),
            store_id=self.settings.ziniao_shopee_store_id,
            store_ids={
                "tiktok_shop_th_help": (
                    self.settings.ziniao_tiktok_store_id
                ),
            },
            sources=DEFAULT_SOURCES,
            request_delay_ms=self.settings.knowledge_request_delay_ms,
            max_pages=self.settings.knowledge_max_pages_per_run,
        ).update()

    def handle_incident(self, incident: Incident, notify: bool) -> None:
        self.state.record_incident(incident)
        if not notify or not self.state.should_notify(incident):
            return
        try:
            delivery = deliver_incident(
                stage=incident.stage,
                report_date=date.fromisoformat(incident.report_date),
                message=incident.message,
                log_path=self.state.fault_log,
                webhook_url=self.settings.feishu_webhook,
            )
        except Exception:  # noqa: BLE001 - 通知失败不得覆盖原始故障
            return
        if delivery.status == "sent":
            self.state.mark_notified(incident)

    def occurred_at(self) -> str:
        return datetime.now(
            ZoneInfo(self.settings.timezone)
        ).isoformat()


class DailyRunService:
    def __init__(self, dependencies: DailyRunDependencies) -> None:
        self.dependencies = dependencies

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        database: Database | None = None,
    ) -> DailyRunService:
        selected_database = database or Database(settings.database_path)
        return cls(
            ProductionDailyRunDependencies(settings, selected_database)
        )

    def run(
        self,
        *,
        execution_date: date,
        report_date: date,
        mode: str,
        notify: bool,
    ) -> DailyRunResult:
        run_id = str(uuid.uuid4())
        messages: list[str] = []
        rate_failed = False
        income_scan_failed = False
        order_sync_failed = False
        try:
            self.dependencies.prepare()
        except Exception as error:  # noqa: BLE001 - 初始化故障必须落盘
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "initialization",
                error,
                notify,
            )
            messages.append(self._error_message("initialization", error))
            return self._failed_result(messages)

        try:
            resolution = self.dependencies.resolve_exchange_rate(
                execution_date,
                report_date,
                mode,
            )
            if resolution is not None:
                messages.append(
                    f"exchange_rate={resolution.status} "
                    f"THB/CNY={resolution.rate} "
                    f"source_date={resolution.source_date.isoformat()}"
                )
        except Exception as error:  # noqa: BLE001 - 汇率故障不得阻断采集
            rate_failed = True
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "exchange_rate",
                error,
                notify,
            )
            messages.append(
                "exchange_rate=unavailable "
                f"error={type(error).__name__}: {error}"
            )

        try:
            income_scan = self.dependencies.scan_income(mode)
            if income_scan is not None:
                income_status = (
                    "partial"
                    if income_scan.quarantined or income_scan.failed
                    else "ok"
                )
                messages.append(
                    f"income_scan={income_status} "
                    f"discovered={income_scan.discovered} "
                    f"imported={income_scan.imported} "
                    f"revised={income_scan.revised} "
                    f"skipped={income_scan.skipped} "
                    f"quarantined={income_scan.quarantined} "
                    f"failed={income_scan.failed}"
                )
                if income_status == "partial":
                    income_scan_failed = True
                    quality_error = RuntimeError(
                        "income scan "
                        f"quarantined={income_scan.quarantined} "
                        f"failed={income_scan.failed}"
                    )
                    self._record_incident(
                        run_id,
                        execution_date,
                        report_date,
                        "income_scan",
                        quality_error,
                        notify,
                        error_type="IncomeScanPartial",
                    )
        except Exception as error:  # noqa: BLE001 - PDF 扫描故障不得阻断采集
            income_scan_failed = True
            safe_error = RuntimeError("income_scan_failed")
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "income_scan",
                safe_error,
                notify,
                error_type=type(error).__name__,
            )
            messages.append("income_scan=failed error=income_scan_failed")

        try:
            self.dependencies.check_bridge(mode)
        except Exception as error:  # noqa: BLE001 - Bridge 是硬门禁
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "bridge",
                error,
                notify,
            )
            messages.append(self._error_message("bridge", error))
            return self._failed_result(messages)

        try:
            order_sync = self.dependencies.sync_orders(report_date, mode)
            if order_sync is not None:
                messages.append(
                    f"order_sync={order_sync.status} "
                    f"orders={order_sync.order_count} "
                    f"skus={order_sync.sku_count} "
                    f"rejected={order_sync.rejected_count} "
                    f"refunds={order_sync.refund_count} "
                    f"logistics={order_sync.logistics_count} "
                    "inventory_snapshots="
                    f"{order_sync.inventory_snapshot_count}"
                )
                if order_sync.status != "success":
                    order_sync_failed = True
                    quality_error = RuntimeError(
                        f"{order_sync.rejected_count} record(s) rejected"
                    )
                    self._record_incident(
                        run_id,
                        execution_date,
                        report_date,
                        "order_sync",
                        quality_error,
                        notify,
                        error_type="OrderSyncPartial",
                    )
        except Exception as error:  # noqa: BLE001 - 订单同步失败不阻断广告日报
            order_sync_failed = True
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "order_sync",
                error,
                notify,
            )
            messages.append(self._error_message("order_sync", error))

        try:
            collection = self.dependencies.collect(report_date, mode)
        except Exception as error:  # noqa: BLE001 - 采集框架故障必须落盘
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "collection",
                error,
                notify,
            )
            messages.append(self._error_message("collection", error))
            return self._failed_result(messages)

        collection_errors = []
        for failure in collection.failures:
            collection_errors.append(failure.platform)
            error = RuntimeError(failure.message)
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "collection",
                error,
                notify,
                error_type=failure.error_type,
            )
            messages.append(
                f"{failure.platform} collection failed: "
                f"{failure.error_type}: {failure.message}"
            )

        try:
            analysis = self.dependencies.analyze_and_report(
                report_date,
                mode,
                notify,
            )
        except Exception as error:  # noqa: BLE001 - 分析故障必须落盘
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "analysis",
                error,
                notify,
            )
            messages.append(self._error_message("analysis", error))
            return self._failed_result(
                messages,
                collection_errors=tuple(collection_errors),
            )

        try:
            self.dependencies.update_knowledge(mode)
        except Exception as error:  # noqa: BLE001 - 知识更新为非阻塞附加项
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "knowledge",
                error,
                notify,
            )
            messages.append(
                f"knowledge update failed: "
                f"{type(error).__name__}: {error}"
            )

        delivery_failed = analysis.delivery_error is not None
        if analysis.delivery_error:
            error = RuntimeError(analysis.delivery_error)
            self._record_incident(
                run_id,
                execution_date,
                report_date,
                "delivery",
                error,
                notify,
                error_type="DeliveryError",
            )
            messages.append(
                f"delivery=failed error={analysis.delivery_error}"
            )

        partial = (
            rate_failed
            or income_scan_failed
            or order_sync_failed
            or analysis.orders_pending_order_identity > 0
            or bool(collection_errors)
            or delivery_failed
        )
        return DailyRunResult(
            exit_code=2 if partial else 0,
            status="partial" if partial else "ok",
            report_path=analysis.report_path,
            collection_errors=tuple(collection_errors),
            delivery_status=analysis.delivery_status,
            metrics_processed=analysis.metrics_processed,
            recommendations=analysis.recommendations,
            orders_shipped=analysis.orders_shipped,
            orders_supplier_costed=analysis.orders_supplier_costed,
            orders_pending_cost=analysis.orders_pending_cost,
            orders_pending_fulfillment=(
                analysis.orders_pending_fulfillment
            ),
            orders_pending_order_identity=(
                analysis.orders_pending_order_identity
            ),
            messages=tuple(messages),
        )

    def _record_incident(
        self,
        run_id: str,
        execution_date: date,
        report_date: date,
        stage: str,
        error: Exception,
        notify: bool,
        *,
        error_type: str | None = None,
    ) -> None:
        occurred_at = getattr(
            self.dependencies,
            "occurred_at",
            lambda: datetime.now().astimezone().isoformat(),
        )()
        incident = Incident(
            run_id=run_id,
            occurred_at=occurred_at,
            execution_date=execution_date.isoformat(),
            report_date=report_date.isoformat(),
            stage=stage,
            error_type=error_type or type(error).__name__,
            message=str(error),
            exit_code=2,
            notification_status="pending" if notify else "suppressed",
        )
        self.dependencies.handle_incident(incident, notify)

    @staticmethod
    def _error_message(stage: str, error: Exception) -> str:
        return f"{stage}=failed error={type(error).__name__}: {error}"

    @staticmethod
    def _failed_result(
        messages: list[str],
        *,
        collection_errors: tuple[str, ...] = (),
    ) -> DailyRunResult:
        return DailyRunResult(
            exit_code=2,
            status="failed",
            report_path=None,
            collection_errors=collection_errors,
            messages=tuple(messages),
        )

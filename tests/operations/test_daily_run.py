import json
from datetime import date

import pytest

import adwatch.operations.daily_run as daily_run_module
from adwatch.config import Settings
from adwatch.domain import Platform
from adwatch.income.service import ScanResult
from adwatch.operations.daily_run import (
    AnalysisOutcome,
    BridgeUnavailable,
    CollectionFailure,
    CollectionOutcome,
    DailyRunResult,
    DailyRunService,
    ProductionDailyRunDependencies,
)
from adwatch.operations.run_state import RunStateStore
from adwatch.orders.ziniao_sync import OrderSyncResult
from adwatch.storage.db import Database


class RecordingDependencies:
    def __init__(
        self,
        tmp_path,
        *,
        bridge_error=None,
        collection_failures=(),
        delivery_error=None,
        rate_error=None,
        order_sync_error=None,
        order_sync_result=None,
        income_scan_error=None,
        income_scan_result=None,
        pending_order_identity=0,
    ):
        self.calls = []
        self.store = RunStateStore(tmp_path)
        self.notifications = []
        self.bridge_error = bridge_error
        self.collection_failures = collection_failures
        self.delivery_error = delivery_error
        self.rate_error = rate_error
        self.order_sync_error = order_sync_error
        self.order_sync_result = order_sync_result
        self.income_scan_error = income_scan_error
        self.income_scan_result = income_scan_result
        self.pending_order_identity = pending_order_identity

    def prepare(self):
        pass

    def resolve_exchange_rate(self, execution_date, report_date, mode):
        self.calls.append("exchange_rate")
        if self.rate_error:
            raise self.rate_error

    def check_bridge(self, mode):
        self.calls.append("bridge")
        if self.bridge_error:
            raise self.bridge_error

    def scan_income(self, mode):
        self.calls.append("income_scan")
        if self.income_scan_error:
            raise self.income_scan_error
        return self.income_scan_result

    def sync_orders(self, report_date, mode):
        self.calls.append("order_sync")
        if self.order_sync_error:
            raise self.order_sync_error
        return self.order_sync_result

    def collect(self, report_date, mode):
        self.calls.append("collection")
        return CollectionOutcome((), self.collection_failures)

    def analyze_and_report(self, report_date, mode, notify):
        self.calls.append("analysis")
        report_path = self.store.fault_log.parent / f"daily-{report_date}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# 日报")
        return AnalysisOutcome(
            report_path=report_path,
            delivery_status="sent" if notify else "suppressed",
            delivery_error=self.delivery_error,
            orders_pending_order_identity=self.pending_order_identity,
        )

    def update_knowledge(self, mode):
        pass

    def handle_incident(self, incident, notify):
        self.store.record_incident(incident)
        if notify:
            self.notifications.append(incident.dedupe_key)


def test_daily_run_orders_rate_bridge_order_sync_collection_and_analysis(tmp_path):
    dependencies = RecordingDependencies(tmp_path)

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=True,
    )

    assert result.exit_code == 0
    assert dependencies.calls[:6] == [
        "exchange_rate",
        "income_scan",
        "bridge",
        "order_sync",
        "collection",
        "analysis",
    ]


def test_disabled_tiktok_is_excluded_from_knowledge_update(
    tmp_path, monkeypatch
):
    captured = {}

    class RecordingKnowledgeService:
        def __init__(self, repository, cli, **kwargs):
            captured.update(kwargs)

        def update(self):
            return None

    monkeypatch.setattr(
        daily_run_module, "KnowledgeService", RecordingKnowledgeService
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="shopee-store",
        ziniao_tiktok_store_id="tiktok-store",
        tiktok_collection_enabled=False,
    )
    dependencies = ProductionDailyRunDependencies(
        settings, Database(settings.database_path)
    )

    dependencies.update_knowledge("ziniao")

    assert [source.key for source in captured["sources"]] == [
        "shopee_th_seller_education"
    ]
    assert captured["store_ids"] == {}


def test_daily_run_summary_reports_pending_order_identity():
    result = DailyRunResult(
        exit_code=2,
        status="partial",
        report_path=None,
        collection_errors=(),
        orders_pending_order_identity=1,
    )

    assert "orders_pending_order_identity=1" in result.summary_line()


def test_pending_order_identity_marks_daily_run_partial(tmp_path):
    dependencies = RecordingDependencies(tmp_path, pending_order_identity=1)

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 8, 4),
        report_date=date(2026, 8, 3),
        mode="ziniao",
        notify=False,
    )

    assert result.status == "partial"
    assert result.exit_code == 2


def test_order_sync_failure_is_partial_and_does_not_block_ads(tmp_path):
    dependencies = RecordingDependencies(
        tmp_path,
        order_sync_error=RuntimeError("Shopee orders unavailable"),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 8, 4),
        report_date=date(2026, 8, 3),
        mode="ziniao",
        notify=False,
    )

    assert result.status == "partial"
    assert dependencies.calls == [
        "exchange_rate",
        "income_scan",
        "bridge",
        "order_sync",
        "collection",
        "analysis",
    ]
    incident = json.loads(dependencies.store.fault_log.read_text().splitlines()[0])
    assert incident["stage"] == "order_sync"


def test_order_sync_message_reports_all_fact_counts(tmp_path):
    dependencies = RecordingDependencies(
        tmp_path,
        order_sync_result=OrderSyncResult(
            status="success",
            order_count=3,
            sku_count=4,
            rejected_count=0,
            refund_count=1,
            logistics_count=2,
            inventory_snapshot_count=5,
        ),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 8, 4),
        report_date=date(2026, 8, 3),
        mode="ziniao",
        notify=False,
    )

    assert result.status == "ok"
    assert (
        "order_sync=success orders=3 skus=4 rejected=0 "
        "refunds=1 logistics=2 inventory_snapshots=5"
    ) in result.messages


def test_partial_order_sync_marks_daily_partial_and_logs_quality_incident(
    tmp_path,
):
    dependencies = RecordingDependencies(
        tmp_path,
        order_sync_result=OrderSyncResult(
            status="partial",
            order_count=3,
            sku_count=4,
            rejected_count=1,
        ),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 8, 4),
        report_date=date(2026, 8, 3),
        mode="ziniao",
        notify=False,
    )

    assert result.status == "partial"
    incident = json.loads(
        dependencies.store.fault_log.read_text().splitlines()[0]
    )
    assert incident["stage"] == "order_sync"
    assert "1 record(s) rejected" in incident["message"]


def test_bridge_failure_stops_collection_and_logs_incident(tmp_path):
    dependencies = RecordingDependencies(
        tmp_path,
        bridge_error=BridgeUnavailable("无法连接紫鸟 Bridge"),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=True,
    )

    assert result.exit_code == 2
    assert "collection" not in dependencies.calls
    logged = json.loads(
        dependencies.store.fault_log.read_text().splitlines()[0]
    )
    assert logged["stage"] == "bridge"
    assert len(dependencies.notifications) == 1


def test_no_notify_still_logs_fault_without_external_notification(tmp_path):
    dependencies = RecordingDependencies(
        tmp_path,
        bridge_error=BridgeUnavailable("无法连接紫鸟 Bridge"),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=False,
    )

    assert result.exit_code == 2
    assert dependencies.store.fault_log.exists()
    assert dependencies.notifications == []


def test_platform_collection_failure_keeps_analysis_and_is_partial(tmp_path):
    failure = CollectionFailure(
        platform="tiktok",
        error_type="RuntimeError",
        message="TikTok page changed",
    )
    dependencies = RecordingDependencies(
        tmp_path,
        collection_failures=(failure,),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=True,
    )

    assert result.status == "partial"
    assert result.exit_code == 2
    assert "analysis" in dependencies.calls


def test_unexpected_rate_source_failure_still_continues_collection(
    tmp_path,
):
    dependencies = RecordingDependencies(
        tmp_path,
        rate_error=RuntimeError("malformed ECB response"),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=False,
    )

    assert result.status == "partial"
    assert dependencies.calls[:6] == [
        "exchange_rate",
        "income_scan",
        "bridge",
        "order_sync",
        "collection",
        "analysis",
    ]
    logged = json.loads(
        dependencies.store.fault_log.read_text().splitlines()[0]
    )
    assert logged["stage"] == "exchange_rate"


def test_delivery_failure_is_logged_and_not_marked_successful(tmp_path):
    dependencies = RecordingDependencies(
        tmp_path,
        delivery_error="OSError: network unavailable",
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        mode="ziniao",
        notify=True,
    )

    assert result.status == "partial"
    logged = [
        json.loads(line)
        for line in dependencies.store.fault_log.read_text().splitlines()
    ]
    assert logged[-1]["stage"] == "delivery"


def test_income_scan_quality_failure_is_partial_but_does_not_block_collection(
    tmp_path,
):
    dependencies = RecordingDependencies(
        tmp_path,
        income_scan_result=ScanResult(
            discovered=2,
            imported=1,
            quarantined=1,
        ),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 8, 4),
        report_date=date(2026, 8, 3),
        mode="ziniao",
        notify=False,
    )

    assert result.status == "partial"
    assert "collection" in dependencies.calls
    incident = json.loads(
        dependencies.store.fault_log.read_text().splitlines()[0]
    )
    assert incident["stage"] == "income_scan"
    assert "quarantined=1 failed=0" in incident["message"]


def test_income_scan_exception_is_partial_but_order_sync_still_runs(tmp_path):
    dependencies = RecordingDependencies(
        tmp_path,
        income_scan_error=RuntimeError(
            "permission denied: /Users/private/income"
        ),
    )

    result = DailyRunService(dependencies).run(
        execution_date=date(2026, 8, 4),
        report_date=date(2026, 8, 3),
        mode="ziniao",
        notify=False,
    )

    assert result.status == "partial"
    assert dependencies.calls[:4] == [
        "exchange_rate",
        "income_scan",
        "bridge",
        "order_sync",
    ]
    incident = json.loads(
        dependencies.store.fault_log.read_text().splitlines()[0]
    )
    assert incident["message"] == "income_scan_failed"
    assert "/Users/" not in incident["message"]
    assert all("/Users/" not in message for message in result.messages)


def test_bridge_retries_use_central_configuration(
    tmp_path, monkeypatch
):
    calls = []
    sleeps = []

    class OfflineBridge:
        def get_store_list(self):
            calls.append("bridge")
            raise OSError("offline")

    monkeypatch.setattr(
        "adwatch.operations.daily_run.ZiniaoCliClient",
        OfflineBridge,
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
        bridge_retries=3,
        bridge_retry_delay_seconds=2,
    )
    dependencies = ProductionDailyRunDependencies(
        settings,
        Database(settings.database_path),
        sleeper=sleeps.append,
    )

    with pytest.raises(BridgeUnavailable):
        dependencies.check_bridge("ziniao")

    assert calls == ["bridge", "bridge", "bridge"]
    assert sleeps == [2, 2]


def test_production_collection_skips_disabled_tiktok(
    tmp_path, monkeypatch
):
    platforms = []

    class RecordingCollector:
        source = "ziniao"

        def __init__(self, *args, **kwargs):
            self.platform = Platform.SHOPEE

        def collect(self, data_date):
            return []

    class FakeCollectorRegistry:
        def get_collector(self, platform):
            p = Platform(platform)
            platforms.append(p.value)
            return RecordingCollector()

        def report_success(self, platform):
            pass

        def report_failure(self, platform):
            pass

    # 替换 CollectorRegistry 为 FakeCollectorRegistry
    monkeypatch.setattr(
        "adwatch.operations.daily_run.CollectorRegistry",
        lambda *args, **kwargs: FakeCollectorRegistry(),
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="222",
        tiktok_collection_enabled=False,
    )
    database = Database(settings.database_path)
    database.migrate()

    outcome = ProductionDailyRunDependencies(
        settings, database
    ).collect(date(2026, 8, 1), "ziniao")

    assert platforms == ["shopee"]
    assert outcome.failures == ()


def test_production_order_sync_uses_configured_history_lookback(
    tmp_path,
    monkeypatch,
):
    calls = []

    class RecordingSync:
        def __init__(self, repository, reader, *, store, timezone):
            assert store == "shop"
            assert timezone == "Asia/Shanghai"

        def sync(self, *, start, end):
            calls.append((start, end))
            return OrderSyncResult("success", 0, 0, 0)

    monkeypatch.setattr(
        daily_run_module,
        "ShopeeOrderSync",
        RecordingSync,
    )
    monkeypatch.setattr(
        daily_run_module,
        "ZiniaoShopeeReadClient",
        lambda client, store_id: object(),
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="222",
        ziniao_shopee_store_name="shop",
        tiktok_collection_enabled=False,
        shopee_history_lookback_days=3,
    )
    database = Database(settings.database_path)
    database.migrate()
    dependencies = ProductionDailyRunDependencies(settings, database)

    dependencies.sync_orders(date(2026, 8, 4), "ziniao")

    assert calls == [(date(2026, 8, 1), date(2026, 8, 4))]

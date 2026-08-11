from datetime import date
from decimal import Decimal

import adwatch.cli
from adwatch.cli import main
from adwatch.config import Settings
from adwatch.domain import DailyAdMetric, Platform
from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryService
from adwatch.operations import daily_run
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.models import PlatformOrderLine
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


def _patch_ready_bridge_and_knowledge(monkeypatch):
    class ReadyBridge:
        def get_store_list(self):
            return []

    monkeypatch.setattr(daily_run, "ZiniaoCliClient", ReadyBridge)
    monkeypatch.setattr(
        daily_run.ProductionDailyRunDependencies,
        "update_knowledge",
        lambda self, mode: None,
    )
    monkeypatch.setattr(
        daily_run.ProductionDailyRunDependencies,
        "sync_orders",
        lambda self, report_date, mode: None,
    )


def _patch_execution_day_rate(monkeypatch):
    def resolve_rate(*args, **kwargs):
        return type(
            "Resolution",
            (),
            {
                "status": "execution_day",
                "rate": Decimal("0.201"),
                "source_date": date(2026, 7, 30),
            },
        )()

    monkeypatch.setattr(
        daily_run,
        "resolve_scheduled_exchange_rate",
        resolve_rate,
    )


def test_daily_run_creates_all_local_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("ADWATCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_WEBHOOK", "")
    assert main(["run", "daily", "--mode", "mock", "--date", "2026-07-22"]) == 0
    assert (tmp_path / "adwatch.sqlite3").exists()
    assert (tmp_path / "reports" / "quality-2026-07-22.json").exists()
    assert (tmp_path / "reports" / "daily-2026-07-22.md").exists()


def test_daily_run_no_notify_still_creates_local_report(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(
        data_dir=tmp_path,
        feishu_webhook="https://example.test/hook/secret",
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)

    assert (
        main(
            [
                "run",
                "daily",
                "--mode",
                "mock",
                "--date",
                "2026-07-22",
                "--no-notify",
            ]
        )
        == 0
    )

    assert (tmp_path / "reports" / "daily-2026-07-22.md").exists()
    assert "delivery=suppressed" in capsys.readouterr().out


def test_daily_run_ensures_exchange_rate_before_analysis(
    tmp_path, monkeypatch
):
    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
        tiktok_collection_enabled=True,
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    _patch_ready_bridge_and_knowledge(monkeypatch)

    class FakeCollectorRegistry:
        def get_collector(self, platform):
            return type(
                "EmptyCollector",
                (),
                {
                    "source": "ziniao",
                    "platform": Platform(platform),
                    "collect": lambda self, data_date: [],
                },
            )()

        def report_success(self, platform):
            pass

        def report_failure(self, platform):
            pass

    monkeypatch.setattr(
        daily_run,
        "CollectorRegistry",
        lambda *args, **kwargs: FakeCollectorRegistry(),
    )
    calls = []
    original_run = daily_run.AnalysisService.run

    def ensure_rate(*args, **kwargs):
        calls.append("exchange_rate")
        return type(
            "Resolution",
            (),
            {
                "status": "local_fallback",
                "rate": Decimal("0.201"),
                "source_date": date(2026, 7, 21),
            },
        )()

    def analyze(self, data_date):
        calls.append("analysis")
        return original_run(self, data_date)

    monkeypatch.setattr(
        daily_run, "resolve_scheduled_exchange_rate", ensure_rate
    )
    monkeypatch.setattr(daily_run.AnalysisService, "run", analyze)

    assert main(
        ["run", "daily", "--mode", "ziniao", "--date", "2026-07-22"]
    ) == 0
    assert calls[:2] == ["exchange_rate", "analysis"]


def test_daily_run_continues_when_exchange_rate_is_unavailable(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    _patch_ready_bridge_and_knowledge(monkeypatch)

    class FakeCollectorRegistry2:
        def get_collector(self, platform):
            return type(
                "EmptyCollector",
                (),
                {
                    "source": "ziniao",
                    "platform": Platform(platform),
                    "collect": lambda self, data_date: [],
                },
            )()

        def report_success(self, platform):
            pass

        def report_failure(self, platform):
            pass

    monkeypatch.setattr(
        daily_run,
        "CollectorRegistry",
        lambda *args, **kwargs: FakeCollectorRegistry2(),
    )

    def unavailable(*args, **kwargs):
        raise ValueError("latest THB/CNY exchange rate is older than 7 days")

    monkeypatch.setattr(
        daily_run, "resolve_scheduled_exchange_rate", unavailable
    )

    assert main(
        ["run", "daily", "--mode", "ziniao", "--date", "2026-07-22"]
    ) == 2
    output = capsys.readouterr().out
    assert "exchange_rate=unavailable" in output
    assert "daily_run=partial" in output


def test_daily_run_syncs_existing_platform_orders_before_analysis(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    database = Database(settings.database_path)
    database.migrate()
    FulfillmentService(database).set_policy(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        mode="stocked",
        supply_status="available",
    )
    InventoryService(database).receive_purchase(
        receipt_id="PO-1",
        supplier="factory",
        received_on=date(2026, 7, 1),
        lines=(PurchaseLine("SKU-1", 10, Decimal(5)),),
        actor="yl",
    )
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders(
        (
            PlatformOrderLine(
                "shopee",
                "shop",
                "ORDER-1",
                "item",
                "model",
                "SKU-1",
                "1 bag",
                "Product",
                1,
                Decimal(20),
                "THB",
                "completed",
                "delivered",
                "",
                date(2026, 7, 22),
                date(2026, 7, 22),
            ),
        )
    )

    assert main(["run", "daily", "--mode", "mock", "--date", "2026-07-22"]) == 0
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM order_cost_snapshots").fetchone()[
                0
            ]
            == 1
        )


def test_daily_ziniao_run_uses_real_collectors_and_marks_real_report(
    tmp_path, monkeypatch, capsys
):
    class RealCollector:
        source = "ziniao"

        def __init__(self, settings, platform):
            self.platform = platform

        def collect(self, data_date):
            if self.platform.value == "tiktok":
                return []
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="虾皮泰国",
                    account_id="222",
                    campaign_id="Shop GMV Max",
                    sku_id="SKU-1",
                    data_date=data_date,
                    currency="THB",
                    spend=Decimal(10),
                    attributed_gmv=Decimal(30),
                    orders=1,
                    source=self.source,
                )
            ]

    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    _patch_ready_bridge_and_knowledge(monkeypatch)
    _patch_execution_day_rate(monkeypatch)
    class FakeCollectorRegistry:
        def get_collector(self, platform):
            return RealCollector(None, Platform(platform))

        def report_success(self, platform):
            pass

        def report_failure(self, platform):
            pass

    monkeypatch.setattr(
        daily_run,
        "CollectorRegistry",
        lambda *args, **kwargs: FakeCollectorRegistry(),
    )

    assert main(["run", "daily", "--mode", "ziniao", "--date", "2026-07-22"]) == 0

    report = (tmp_path / "reports" / "daily-2026-07-22.md").read_text()
    assert "【真实数据】" in report
    assert "【模拟数据】" not in report
    assert "daily_run=ok metrics=1" in capsys.readouterr().out


def test_daily_run_keeps_shopee_data_when_tiktok_collection_fails(
    tmp_path, monkeypatch, capsys
):
    class PartiallyFailingCollector:
        source = "ziniao"

        def __init__(self, settings, platform):
            self.platform = platform

        def collect(self, data_date):
            if self.platform.value == "tiktok":
                raise RuntimeError("TikTok page changed")
            return [
                DailyAdMetric(
                    platform=self.platform,
                    store="虾皮泰国",
                    account_id="222",
                    campaign_id="Shop GMV Max",
                    sku_id="__ALL__",
                    data_date=data_date,
                    currency="THB",
                    spend=Decimal(10),
                    attributed_gmv=Decimal(30),
                    orders=1,
                    source=self.source,
                )
            ]

    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="111",
        ziniao_shopee_store_id="222",
        tiktok_collection_enabled=True,
    )
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    _patch_ready_bridge_and_knowledge(monkeypatch)
    _patch_execution_day_rate(monkeypatch)

    class FakeCollectorRegistry:
        def get_collector(self, platform):
            return PartiallyFailingCollector(None, Platform(platform))

        def report_success(self, platform):
            pass

        def report_failure(self, platform):
            pass

    monkeypatch.setattr(
        daily_run,
        "CollectorRegistry",
        lambda *args, **kwargs: FakeCollectorRegistry(),
    )

    result = main(["run", "daily", "--mode", "ziniao", "--date", "2026-07-22"])

    assert result == 2
    output = capsys.readouterr().out
    assert "tiktok collection failed: RuntimeError" in output
    assert "daily_run=partial metrics=1" in output
    database = adwatch.cli.Database(settings.database_path)
    with database.connect() as connection:
        platforms = connection.execute(
            "SELECT DISTINCT platform FROM daily_ad_metrics"
        ).fetchall()
    assert [row[0] for row in platforms] == ["shopee"]

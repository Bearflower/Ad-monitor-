from datetime import date
from decimal import Decimal

import adwatch.cli
from adwatch.cli import main
from adwatch.config import Settings
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


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
    assert "<key>Hour</key><integer>9</integer>" in output
    assert (
        "<key>LimitLoadToSessionType</key><string>Aqua</string>"
        in output
    )


def test_backup_cli_creates_verified_snapshot(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0
    destination = tmp_path / "backup.sqlite3"

    assert main(["backup", "create", "--output", str(destination)]) == 0

    assert destination.exists()
    assert "integrity=ok" in capsys.readouterr().out


def test_readiness_cli_reports_pending_without_enabling_live(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        adwatch.cli, "_ziniao_bridge_ready", lambda: False
    )

    assert main(["readiness"]) == 2
    output = capsys.readouterr().out
    assert "ziniao_bridge=pending_external" in output
    assert "live_writes=blocked" in output


def test_monthly_report_cli_writes_local_file(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0

    assert main(["report", "monthly", "--month", "2026-07"]) == 0

    path = tmp_path / "reports" / "monthly-2026-07.md"
    assert path.exists()
    assert "# 广告月报 2026-07" in path.read_text()
    assert str(path) in capsys.readouterr().out


def test_approval_server_refuses_to_start_without_secret(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)

    assert main(["approval", "serve"]) == 2
    assert "FEISHU_CALLBACK_SECRET" in capsys.readouterr().out


def test_launch_checklist_cli_writes_markdown(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "_ziniao_bridge_ready", lambda: False)

    assert main(["launch-checklist", "--format", "markdown"]) == 0

    output = capsys.readouterr().out
    assert "# Adwatch 上线待办" in output
    assert "ziniao_bridge" in output
    assert "live_allowlist" in output


def test_reconciliation_csv_import_and_report(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    source = tmp_path / "reconcile.csv"
    source.write_text(
        "field,expected,actual,category\n"
        "spend,400.00,400.00,money\n"
        "gmv,1066.00,1066.00,money\n"
        "orders,5,5,count\n"
        "roas,2.67,2.6650,ratio\n",
        encoding="utf-8",
    )
    assert main(
        [
            "reconcile",
            "import",
            "--platform",
            "shopee",
            "--store",
            "shop",
            "--date",
            "2026-07-28",
            "--file",
            str(source),
        ]
    ) == 0
    assert "accuracy=1.0000" in capsys.readouterr().out
    assert main(
        [
            "reconcile",
            "report",
            "--platform",
            "shopee",
            "--store",
            "shop",
            "--from",
            "2026-07-28",
            "--to",
            "2026-07-28",
        ]
    ) == 0
    assert "accuracy=1.0000" in capsys.readouterr().out


def test_strategy_replay_reports_pending_when_history_is_missing(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    result = main(
        [
            "strategy",
            "replay",
            "--platform",
            "shopee",
            "--store",
            "shop",
            "--campaign",
            "C-1",
            "--from",
            "2026-07-01",
            "--to",
            "2026-07-28",
        ]
    )
    assert result == 2
    assert "pending_data" in capsys.readouterr().out


def test_order_costs_satisfy_launch_business_cost_gate(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "_ziniao_bridge_ready", lambda: False)
    database = Database(settings.database_path)
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_cost_lines(
                platform, store, order_id, sku_id, order_date, quantity,
                unit_cost_cny, line_cost_cny, source_file
            ) VALUES (
                'shopee', 'store', 'order', '1 bag', '2026-07-23',
                1, '5', '5', 'orders.xlsx'
            )
            """
        )

    assert main(["launch-checklist", "--format", "markdown"]) == 0

    assert "business_costs" not in capsys.readouterr().out


def test_supplier_sku_facts_clear_mapping_refund_and_inventory_gates(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "_ziniao_bridge_ready", lambda: True)
    database = Database(settings.database_path)
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO sku_cost_history VALUES(
              'shopee','shop','SKU-1','2026-07-01','5','',
              '2026-07-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO sku_fulfillment_history VALUES(
              'shopee','shop','SKU-1','2026-07-01',
              'supplier_fulfilled','available','',
              '2026-07-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','ORDER-1','SKU-1','SKU-1','SKU-1',
              '1 bag','Product',1,'100','THB','completed','delivered',
              'returned','2026-07-23','2026-07-24T00:00:00Z')
            """
        )

    assert main(["launch-checklist", "--format", "markdown"]) == 0

    output = capsys.readouterr().out
    assert "business_costs" not in output
    assert "sku_mapping" not in output
    assert "refund_source" not in output
    assert "inventory_source" not in output


def test_cancelled_order_without_sku_facts_does_not_block_mapping_gate(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "_ziniao_bridge_ready", lambda: True)
    database = Database(settings.database_path)
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','CANCELLED-1','OLD-SKU','OLD-SKU','OLD-SKU',
              '1 bag','Product',1,'100','THB','cancelled','cancelled',
              '','2026-07-23','2026-07-24T00:00:00Z')
            """
        )

    assert main(["launch-checklist", "--format", "markdown"]) == 0

    assert "sku_mapping" not in capsys.readouterr().out


def test_business_sync_exchange_rates(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        adwatch.cli.EcbExchangeRateSource,
        "fetch_range",
        lambda self, currency, start, end: {
            start: Decimal("0.201"),
            end: Decimal("0.202"),
        },
    )

    assert (
        main(
            [
                "business",
                "sync-exchange-rates",
                "--currency",
                "THB",
                "--from",
                "2026-07-26",
                "--to",
                "2026-07-27",
            ]
        )
        == 0
    )

    assert "Synced 2 THB/CNY exchange rates" in capsys.readouterr().out


def test_business_sync_exchange_rates_reports_network_failure(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        adwatch.cli.EcbExchangeRateSource,
        "fetch_range",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    result = main(
        [
            "business",
            "sync-exchange-rates",
            "--from",
            "2026-07-26",
            "--to",
            "2026-07-27",
        ]
    )

    assert result == 2
    assert "Exchange-rate sync failed: offline" in capsys.readouterr().out


def test_business_sync_orders_reports_pending_facts(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    database = Database(settings.database_path)
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','ORDER-1','item','model','SKU-1',
              '1 bag','Product',1,'100','THB','completed','delivered','',
              '2026-07-23','2026-07-24T00:00:00Z')
            """
        )

    assert main(["business", "sync-orders"]) == 2

    output = capsys.readouterr().out
    assert "pending_fulfillment=1" in output


def test_business_fulfillment_cli_sets_single_and_bulk_policies(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    database = Database(settings.database_path)
    database.migrate()
    OrderRepository(database).set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 4, 1),
        unit_cost_cny=Decimal(5),
    )

    assert (
        main(
            [
                "business",
                "set-fulfillment",
                "--platform",
                "shopee",
                "--store",
                "shop",
                "--sku",
                "SKU-1",
                "--effective-date",
                "2026-08-01",
                "--mode",
                "stocked",
                "--supply-status",
                "available",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "business",
                "mark-current-skus-supplier-fulfilled",
                "--platform",
                "shopee",
                "--store",
                "shop",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "mode=stocked" in output
    assert "marked_supplier_fulfilled=1" in output


def test_weekly_report_cli_writes_local_file(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0

    assert main(["report", "weekly", "--end", "2026-07-26"]) == 0

    path = tmp_path / "reports" / "weekly-2026-07-26.md"
    assert path.exists()
    assert "# 广告周报" in path.read_text()


def test_daily_report_cli_writes_local_file(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    assert main(["init"]) == 0

    assert main(["report", "daily", "--date", "2026-07-26"]) == 0

    path = tmp_path / "reports" / "daily-2026-07-26.md"
    assert path.exists()
    assert "# 广告经营日报｜2026-07-26" in path.read_text()
    assert str(path) in capsys.readouterr().out


def test_backup_verify_cli_rejects_corrupt_database(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not sqlite")

    assert main(["backup", "verify", "--path", str(path)]) == 2

    assert "verification failed" in capsys.readouterr().out.lower()


def test_live_execution_cli_refuses_before_page_access(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path, live_writes=False)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)

    assert (
        main(
            [
                "execute",
                "live",
                "--approval-id",
                "approval-1",
                "--idempotency-key",
                "run-1",
                "--expected-before",
                '{"budget":"100"}',
            ]
        )
        == 2
    )

    assert "ADWATCH_LIVE_WRITES is disabled" in capsys.readouterr().out


def test_shadow_execution_cli_runs_safe_executor(
    tmp_path, monkeypatch, capsys
):
    settings = Settings(data_dir=tmp_path)
    calls = {}

    class FakeResult:
        audit_id = "audit-1"
        status = "succeeded"

    class FakeExecutor:
        def __init__(self, database, backend):
            calls["backend_mode"] = backend.mode

        def execute(self, approval_id, *, idempotency_key, expected_before):
            calls.update(
                approval_id=approval_id,
                idempotency_key=idempotency_key,
                expected_before=expected_before,
            )
            return FakeResult()

    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(adwatch.cli, "SafeExecutor", FakeExecutor)

    assert (
        main(
            [
                "execute",
                "shadow",
                "--approval-id",
                "approval-1",
                "--idempotency-key",
                "run-1",
                "--expected-before",
                '{"budget":"100"}',
            ]
        )
        == 0
    )

    assert calls == {
        "backend_mode": "shadow",
        "approval_id": "approval-1",
        "idempotency_key": "run-1",
        "expected_before": {"budget": "100"},
    }
    assert "audit=audit-1 status=succeeded" in capsys.readouterr().out


def test_activation_register_and_list_cli(tmp_path, monkeypatch, capsys):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(adwatch.cli.Settings, "from_env", lambda: settings)
    selectors = tmp_path / "selectors.json"
    selectors.write_text(
        '{"value":"#value","stage":"#stage","submit":"#submit"}'
    )
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"before")
    after.write_bytes(b"after")

    assert (
        main(
            [
                "activation",
                "register",
                "--platform",
                "shopee",
                "--action",
                "reduce_budget",
                "--version",
                "v1",
                "--store-id",
                "store-1",
                "--selectors-file",
                str(selectors),
                "--activated-by",
                "boss",
                "--evidence-before",
                str(before),
                "--evidence-after",
                str(after),
            ]
        )
        == 0
    )
    assert main(["activation", "list"]) == 0

    output = capsys.readouterr().out
    assert "shopee/reduce_budget" in output
    assert "version=v1" in output

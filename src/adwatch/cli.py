import argparse
import csv
import json
import shutil
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from adwatch.accounting.excel_preview import ExcelPrototypePreview
from adwatch.analytics.business_inputs import (
    BusinessInputError,
    export_business_template,
    import_business_inputs,
    import_minimal_business_inputs,
)
from adwatch.analytics.order_costs import (
    import_order_costs,
    map_store,
    order_cost_summary,
)
from adwatch.analytics.recalculation import AnalysisRecalculationService
from adwatch.analytics.service import AnalysisService
from adwatch.analytics.sku_cost_workbook import (
    export_pending_sku_costs,
    import_sku_costs,
)
from adwatch.approval.server import serve_callback
from adwatch.collectors.mock import MockCollector
from adwatch.collectors.ziniao import ZiniaoCollector, ZiniaoNotConfigured
from adwatch.collectors.ziniao_client import (
    ZiniaoApiError,
    ZiniaoCliClient,
    ZiniaoClient,
)
from adwatch.config import Settings
from adwatch.dashboard.app import serve
from adwatch.domain import Platform
from adwatch.execution.activation import SelectorActivationStore
from adwatch.execution.executor import ExecutionError, SafeExecutor
from adwatch.execution.policy import (
    ALLOWED_ACTIONS,
    ExecutionPolicy,
    PolicyError,
)
from adwatch.execution.ziniao_backend import ZiniaoExecutionBackend
from adwatch.income.repository import FeeStatementRepository
from adwatch.income.service import IncomeImportService
from adwatch.integrations.exchange_rates import (
    EcbExchangeRateSource,
    sync_exchange_rates,
)
from adwatch.knowledge.repository import KnowledgeRepository
from adwatch.knowledge.search import KnowledgeReadModel
from adwatch.knowledge.service import KnowledgeService
from adwatch.knowledge.sources import DEFAULT_SOURCES
from adwatch.operations.backup import create_backup, verify_backup
from adwatch.operations.daily_run import DailyRunService
from adwatch.operations.fact_sync import (
    export_facts,
    export_facts_to_path,
    import_facts,
    publish_facts,
)
from adwatch.operations.launch_checklist import (
    LaunchReadiness,
    build_launch_checklist,
    render_launch_checklist,
)
from adwatch.operations.readiness import readiness_status
from adwatch.operations.scheduled_daily import (
    run_scheduled_daily,
    run_scheduled_weekly,
)
from adwatch.operations.scheduler import (
    ScheduleCommandError,
    ScheduleManager,
    render_launchd_plist,
)
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.orders.shopee_order_export import parse_order_export
from adwatch.orders.sync import OperationsSyncService
from adwatch.orders.ziniao_sync import (
    OrderSyncError,
    ShopeeOrderSync,
    ZiniaoShopeeReadClient,
)
from adwatch.pipeline.runner import PipelineRunner
from adwatch.reconciliation.service import ReconciliationService
from adwatch.reporting.markdown import (
    render_daily_markdown,
    render_monthly_markdown,
    render_weekly_markdown,
)
from adwatch.reporting.quality import write_quality_report
from adwatch.reporting.read_model import ReportReadModel
from adwatch.storage.db import Database
from adwatch.strategy.replay import StrategyReplayService


def build_parser() -> argparse.ArgumentParser:
    local_today = datetime.now().astimezone().date()
    parser = argparse.ArgumentParser(prog="adwatch")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("init")
    collect = subcommands.add_parser("collect")
    collect.add_argument("--mode", choices=("mock", "ziniao"), default="mock")
    collect.add_argument("--date", type=date.fromisoformat, default=local_today)
    subcommands.add_parser("doctor")
    subcommands.add_parser("readiness")
    launch_checklist = subcommands.add_parser("launch-checklist")
    launch_checklist.add_argument(
        "--format", choices=("markdown", "json"), default="markdown"
    )
    report = subcommands.add_parser("report")
    report_commands = report.add_subparsers(dest="report_command")
    daily_report = report_commands.add_parser("daily")
    daily_report.add_argument("--date", type=date.fromisoformat, required=True)
    monthly = report_commands.add_parser("monthly")
    monthly.add_argument("--month", required=True)
    weekly = report_commands.add_parser("weekly")
    weekly.add_argument("--end", type=date.fromisoformat, required=True)
    backup = subcommands.add_parser("backup")
    backup_commands = backup.add_subparsers(dest="backup_command")
    backup_create = backup_commands.add_parser("create")
    backup_create.add_argument("--output", type=Path, required=True)
    backup_verify = backup_commands.add_parser("verify")
    backup_verify.add_argument("--path", type=Path, required=True)
    replicate = subcommands.add_parser("replicate")
    replicate_commands = replicate.add_subparsers(dest="replicate_command")
    replicate_export = replicate_commands.add_parser("export")
    replicate_export.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    replicate_export.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    replicate_export.add_argument("--output", type=Path, required=True)
    replicate_import = replicate_commands.add_parser("import")
    source = replicate_import.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--stdin", action="store_true")
    replicate_publish = replicate_commands.add_parser("publish")
    replicate_publish.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    replicate_publish.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    replicate_publish.add_argument("--target")
    approval = subcommands.add_parser("approval")
    approval_commands = approval.add_subparsers(dest="approval_command")
    approval_serve = approval_commands.add_parser("serve")
    approval_serve.add_argument("--host", default="127.0.0.1")
    approval_serve.add_argument("--port", type=int, default=8787)
    activation = subcommands.add_parser("activation")
    activation_commands = activation.add_subparsers(
        dest="activation_command"
    )
    activation_commands.add_parser("list")
    activation_register = activation_commands.add_parser("register")
    activation_register.add_argument(
        "--platform", choices=("tiktok", "shopee"), required=True
    )
    activation_register.add_argument(
        "--action", choices=sorted(ALLOWED_ACTIONS), required=True
    )
    activation_register.add_argument("--version", required=True)
    activation_register.add_argument("--store-id", required=True)
    activation_register.add_argument(
        "--selectors-file", type=Path, required=True
    )
    activation_register.add_argument("--activated-by", required=True)
    activation_register.add_argument(
        "--evidence-before", type=Path, required=True
    )
    activation_register.add_argument(
        "--evidence-after", type=Path, required=True
    )
    execute = subcommands.add_parser("execute")
    execute.add_argument("execution_mode", choices=("shadow", "live"))
    execute.add_argument("--approval-id", required=True)
    execute.add_argument("--idempotency-key", required=True)
    execute.add_argument("--expected-before", required=True)
    seed = subcommands.add_parser("seed-business-data")
    seed.add_argument("--date", type=date.fromisoformat, default=local_today)
    analyze = subcommands.add_parser("analyze")
    analyze.add_argument("--date", type=date.fromisoformat)
    analyze.add_argument("--from", dest="start", type=date.fromisoformat)
    analyze.add_argument("--to", dest="end", type=date.fromisoformat)
    analyze.add_argument("--output", type=Path)
    business = subcommands.add_parser("business")
    business_commands = business.add_subparsers(dest="business_command")
    export_template = business_commands.add_parser("export-template")
    export_template.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    export_template.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    export_template.add_argument("--output", type=Path, required=True)
    import_inputs = business_commands.add_parser("import")
    import_inputs.add_argument("--file", type=Path, required=True)
    import_minimal = business_commands.add_parser("import-minimal")
    import_minimal.add_argument("--file", type=Path, required=True)
    import_orders = business_commands.add_parser("import-orders")
    import_orders.add_argument("--file", type=Path, required=True)
    import_shopee_orders = business_commands.add_parser(
        "import-shopee-orders"
    )
    import_shopee_orders.add_argument("--file", type=Path, required=True)
    import_shopee_orders.add_argument("--store", required=True)
    map_store_command = business_commands.add_parser("map-store")
    map_store_command.add_argument("--platform", required=True)
    map_store_command.add_argument("--source", required=True)
    map_store_command.add_argument("--target", required=True)
    order_summary = business_commands.add_parser("order-summary")
    order_summary.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    order_summary.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    export_pending = business_commands.add_parser(
        "export-pending-sku-costs"
    )
    export_pending.add_argument("--output", type=Path, required=True)
    import_sku = business_commands.add_parser("import-sku-costs")
    import_sku.add_argument("--file", type=Path, required=True)
    business_commands.add_parser("sync-orders")
    preview_excel = business_commands.add_parser("preview-accounting-excel")
    preview_excel.add_argument("--file", type=Path, required=True)
    preview_excel.add_argument("--format", choices=("text", "json"), default="text")
    sync_rates = business_commands.add_parser("sync-exchange-rates")
    sync_rates.add_argument("--currency", default="THB")
    sync_rates.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    sync_rates.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    set_fulfillment = business_commands.add_parser("set-fulfillment")
    set_fulfillment.add_argument("--platform", required=True)
    set_fulfillment.add_argument("--store", required=True)
    set_fulfillment.add_argument("--sku", required=True)
    set_fulfillment.add_argument(
        "--effective-date", type=date.fromisoformat, required=True
    )
    set_fulfillment.add_argument(
        "--mode",
        choices=("supplier_fulfilled", "stocked"),
        required=True,
    )
    set_fulfillment.add_argument(
        "--supply-status",
        choices=("available", "paused"),
        default="available",
    )
    set_fulfillment.add_argument("--note", default="")
    set_store_fulfillment = business_commands.add_parser(
        "set-store-fulfillment-default"
    )
    set_store_fulfillment.add_argument("--platform", required=True)
    set_store_fulfillment.add_argument("--store", required=True)
    set_store_fulfillment.add_argument(
        "--effective-date", type=date.fromisoformat, required=True
    )
    set_store_fulfillment.add_argument(
        "--mode",
        choices=("supplier_fulfilled", "stocked"),
        required=True,
    )
    set_store_fulfillment.add_argument(
        "--supply-status",
        choices=("available", "paused"),
        default="available",
    )
    set_store_fulfillment.add_argument("--note", default="")
    bulk_fulfillment = business_commands.add_parser(
        "mark-current-skus-supplier-fulfilled"
    )
    bulk_fulfillment.add_argument("--platform", required=True)
    bulk_fulfillment.add_argument("--store", required=True)
    bulk_fulfillment.add_argument("--note", default="现有货盘SKU")
    orders = subcommands.add_parser("orders")
    order_commands = orders.add_subparsers(dest="order_command")
    order_sync = order_commands.add_parser("sync")
    order_sync.add_argument("--platform", choices=("shopee",), required=True)
    order_sync.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    order_sync.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    income = subcommands.add_parser("income")
    income_commands = income.add_subparsers(dest="income_command")
    income_import = income_commands.add_parser("import")
    income_import.add_argument("--file", type=Path, required=True)
    income_import.add_argument(
        "--platform", choices=("shopee", "tiktok"), required=True
    )
    income_commands.add_parser("scan")
    income_commands.add_parser("list")
    run = subcommands.add_parser("run")
    workflows = run.add_subparsers(dest="workflow")
    daily = workflows.add_parser("daily")
    daily.add_argument("--mode", choices=("mock", "ziniao"), default="mock")
    daily.add_argument(
        "--date",
        type=date.fromisoformat,
    )
    daily.add_argument("--no-notify", action="store_true")
    workflows.add_parser("scheduled-daily")
    workflows.add_parser("scheduled-weekly")
    schedule = subcommands.add_parser("schedule")
    schedule.add_argument("--print-launchd", action="store_true")
    schedule_commands = schedule.add_subparsers(dest="schedule_command")
    schedule_commands.add_parser("install")
    schedule_commands.add_parser("status")
    schedule_commands.add_parser("repair")
    schedule_commands.add_parser("uninstall")
    dashboard = subcommands.add_parser("dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--date", type=date.fromisoformat)
    dashboard.add_argument("--allow-remote", action="store_true")
    reconcile = subcommands.add_parser("reconcile")
    reconcile_commands = reconcile.add_subparsers(dest="reconcile_command")
    reconcile_import = reconcile_commands.add_parser("import")
    reconcile_import.add_argument("--platform", required=True)
    reconcile_import.add_argument("--store", required=True)
    reconcile_import.add_argument("--date", type=date.fromisoformat, required=True)
    reconcile_import.add_argument("--file", type=Path, required=True)
    reconcile_report = reconcile_commands.add_parser("report")
    reconcile_report.add_argument("--platform", required=True)
    reconcile_report.add_argument("--store", required=True)
    reconcile_report.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    reconcile_report.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    strategy = subcommands.add_parser("strategy")
    strategy_commands = strategy.add_subparsers(dest="strategy_command")
    strategy_replay = strategy_commands.add_parser("replay")
    strategy_replay.add_argument("--platform", required=True)
    strategy_replay.add_argument("--store", required=True)
    strategy_replay.add_argument("--campaign", required=True)
    strategy_replay.add_argument(
        "--from", dest="start", type=date.fromisoformat, required=True
    )
    strategy_replay.add_argument(
        "--to", dest="end", type=date.fromisoformat, required=True
    )
    knowledge = subcommands.add_parser("knowledge")
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command")
    knowledge_search = knowledge_commands.add_parser("search")
    knowledge_search.add_argument("query")
    knowledge_search.add_argument("--limit", type=int, default=20)
    knowledge_search.add_argument(
        "--format", choices=("json", "table"), default="table"
    )
    knowledge_show = knowledge_commands.add_parser("show")
    knowledge_show.add_argument("document_id")
    knowledge_show.add_argument(
        "--format", choices=("json", "table"), default="table"
    )
    knowledge_commands.add_parser("status")
    knowledge_commands.add_parser("bootstrap")
    knowledge_commands.add_parser("update")
    knowledge_commands.add_parser("enrich")
    knowledge_commands.add_parser("reindex")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if args.command is None:
        parser.print_help()
        return 0
    settings = Settings.from_env()
    database = Database(settings.database_path)
    if args.command == "init":
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        database.migrate()
        print(f"Database initialized: {settings.database_path}")
        return 0
    if args.command == "income":
        database.migrate()
        if args.income_command == "import":
            if args.platform == "tiktok":
                print("income_import=pending_external platform=tiktok")
                return 2
            result = IncomeImportService(
                database,
                scan_root=settings.income_scan_root,
            ).import_file(
                args.file,
                platform=args.platform,
                retry_terminal=True,
            )
            statement = result.statement
            if statement is None:
                print(
                    f"income_import={result.status} "
                    f"platform={args.platform} reason={result.reason_code}"
                )
            else:
                print(
                    f"income_import={result.status} "
                    f"platform={statement.platform} store={statement.store} "
                    f"period={statement.period_start.isoformat()}.."
                    f"{statement.period_end.isoformat()} "
                    f"rate={statement.effective_rate * 100:.4f}% "
                    f"source={statement.source_name} "
                    f"sha={statement.source_sha256[:12]}"
                )
            return 0 if result.status in {"imported", "revised", "skipped"} else 2
        if args.income_command == "scan":
            result = IncomeImportService(
                database,
                scan_root=settings.income_scan_root,
            ).scan()
            status = "partial" if result.quarantined or result.failed else "ok"
            print(
                f"income_scan={status} discovered={result.discovered} "
                f"imported={result.imported} revised={result.revised} "
                f"skipped={result.skipped} "
                f"quarantined={result.quarantined} failed={result.failed}"
            )
            return 2 if status == "partial" else 0
        if args.income_command == "list":
            for row in FeeStatementRepository(database).list_current():
                print(
                    f"{row['platform']} {row['store']} "
                    f"{row['period_start']}..{row['period_end']} "
                    f"rate={Decimal(row['effective_rate']) * 100:.4f}% "
                    f"source={row['source_name']} "
                    f"sha={str(row['source_sha256'])[:12]} "
                    f"version={row['version']}"
                )
            return 0
        parser.print_help()
        return 2
    if args.command == "orders":
        if args.order_command != "sync":
            parser.print_help()
            return 2
        database.migrate()
        if not settings.ziniao_shopee_store_id:
            print("ZINIAO_SHOPEE_STORE_ID is required")
            return 2
        try:
            result = ShopeeOrderSync(
                OrderRepository(database),
                ZiniaoShopeeReadClient(
                    ZiniaoCliClient(),
                    settings.ziniao_shopee_store_id,
                ),
                store=settings.ziniao_shopee_store_name,
                timezone=settings.timezone,
            ).sync(start=args.start, end=args.end)
        except (OrderSyncError, ValueError) as error:
            print(f"order_sync=failed error={error}")
            return 2
        print(
            f"order_sync={result.status} "
            f"orders={result.order_count} skus={result.sku_count} "
            f"rejected={result.rejected_count} "
            f"refunds={result.refund_count} "
            f"logistics={result.logistics_count} "
            f"inventory_snapshots={result.inventory_snapshot_count}"
        )
        return 0
    if args.command == "doctor":
        print(f"SQLite: {settings.database_path}")
        if shutil.which("ziniao-cli"):
            try:
                stores = ZiniaoCliClient().get_store_list()
                print(f"Ziniao CLI: reachable ({len(stores)} stores)")
                return 0
            except (OSError, ZiniaoApiError) as error:
                print(
                    f"Ziniao CLI: unavailable "
                    f"({type(error).__name__}: {error})"
                )
                return 2
        print(f"Ziniao configured: {'yes' if settings.ziniao_ready else 'no'}")
        if settings.ziniao_ready:
            try:
                stores = ZiniaoClient(settings).get_browser_list()
                print(f"Ziniao endpoint: reachable ({len(stores)} stores)")
            except (OSError, ZiniaoApiError) as error:
                print(
                    f"Ziniao endpoint: unavailable "
                    f"({type(error).__name__}: {error})"
                )
                return 2
        return 0
    if args.command == "readiness":
        database.migrate()
        with database.connect() as connection:
            has_tiktok_campaign = bool(
                connection.execute(
                    """
                    SELECT 1 FROM daily_ad_metrics
                    WHERE platform='tiktok' LIMIT 1
                    """
                ).fetchone()
            )
            has_business_costs = bool(
                connection.execute(
                    """
                    SELECT 1
                    WHERE EXISTS(SELECT 1 FROM product_costs)
                       OR EXISTS(SELECT 1 FROM order_cost_lines)
                    """
                ).fetchone()
            )
            has_shopee_campaign = bool(
                connection.execute(
                    """
                    SELECT 1 FROM daily_ad_metrics
                    WHERE platform='shopee' AND source!='mock' LIMIT 1
                    """
                ).fetchone()
            )
        checks = readiness_status(
            bridge_ready=_ziniao_bridge_ready(),
            has_tiktok_campaign=has_tiktok_campaign,
            has_business_costs=has_business_costs,
            feishu_callback_ready=settings.feishu_callback_ready,
            has_shopee_campaign=has_shopee_campaign,
            tiktok_enabled=settings.tiktok_collection_enabled,
            feishu_callback_enabled=bool(settings.feishu_callback_secret),
            live_writes_enabled=settings.live_writes,
            production_publish_ready=bool(settings.publish_ssh_target),
        )
        for check in checks:
            print(f"{check.name}={check.status}")
        return 0 if all(
            item.status in {"ready", "disabled"} for item in checks
        ) else 2
    if args.command == "launch-checklist":
        database.migrate()
        with database.connect() as connection:
            def exists(query: str) -> bool:
                return bool(connection.execute(query).fetchone())

            has_tiktok_campaign = exists(
                """
                SELECT 1 FROM daily_ad_metrics
                WHERE platform='tiktok' AND source!='mock' LIMIT 1
                """
            )
            has_shopee_campaign = exists(
                """
                SELECT 1 FROM daily_ad_metrics
                WHERE platform='shopee' AND source!='mock' LIMIT 1
                """
            )
            has_business_costs = exists(
                """
                SELECT 1
                WHERE EXISTS(SELECT 1 FROM product_costs)
                   OR EXISTS(SELECT 1 FROM order_cost_lines)
                   OR EXISTS(SELECT 1 FROM sku_cost_history)
                   OR EXISTS(SELECT 1 FROM order_cost_snapshots)
                """
            )
            has_sku_mapping = exists(
                """
                SELECT 1
                WHERE EXISTS(
                    SELECT 1 FROM sku_mappings
                    WHERE tiktok_product_id IS NOT NULL
                       OR shopee_product_id IS NOT NULL
                )
                OR (
                    EXISTS(SELECT 1 FROM effective_order_sku_lines)
                    AND NOT EXISTS(
                        SELECT 1 FROM effective_order_sku_lines AS orders
                        WHERE lower(orders.order_status) NOT IN (
                                'cancelled', 'canceled'
                            )
                          AND (
                            TRIM(orders.sku_identity)=''
                            OR (
                              NOT (
                                orders.item_id=orders.seller_sku
                                AND orders.model_id=orders.seller_sku
                              )
                              AND NOT EXISTS(
                                SELECT 1 FROM platform_sku_mappings AS sku
                                WHERE sku.platform=orders.platform
                                  AND sku.item_id=orders.item_id
                                  AND sku.model_id=orders.model_id
                                  AND COALESCE(
                                      (
                                        SELECT alias.canonical_store
                                        FROM store_aliases AS alias
                                        WHERE alias.platform=sku.platform
                                          AND alias.source_store=sku.store
                                      ), sku.store
                                  )=COALESCE(
                                      (
                                        SELECT alias.canonical_store
                                        FROM store_aliases AS alias
                                        WHERE alias.platform=orders.platform
                                          AND alias.source_store=orders.store
                                      ), orders.store
                                  )
                              )
                            )
                          )
                    )
                )
                """
            )
            has_order_cost_coverage = exists(
                """
                SELECT 1
                WHERE EXISTS(SELECT 1 FROM effective_order_sku_lines)
                  AND NOT EXISTS(
                    SELECT 1 FROM effective_order_sku_lines AS orders
                    WHERE lower(orders.order_status) NOT IN (
                            'cancelled', 'canceled'
                        )
                      AND NOT EXISTS(
                        SELECT 1 FROM sku_cost_history AS cost
                        WHERE cost.platform=orders.platform
                          AND cost.seller_sku=orders.sku_identity
                          AND cost.effective_date<=
                              substr(orders.ordered_at, 1, 10)
                          AND COALESCE(
                              (
                                SELECT alias.canonical_store
                                FROM store_aliases AS alias
                                WHERE alias.platform=cost.platform
                                  AND alias.source_store=cost.store
                              ), cost.store
                          )=COALESCE(
                              (
                                SELECT alias.canonical_store
                                FROM store_aliases AS alias
                                WHERE alias.platform=orders.platform
                                  AND alias.source_store=orders.store
                              ), orders.store
                          )
                      )
                  )
                """
            )
            has_order_status_source = exists(
                "SELECT 1 FROM platform_order_lines LIMIT 1"
            )
            inventory_not_applicable = exists(
                """
                SELECT 1
                WHERE (
                    EXISTS(SELECT 1 FROM sku_fulfillment_history)
                    OR EXISTS(SELECT 1 FROM store_fulfillment_history)
                )
                  AND NOT EXISTS(
                      SELECT 1 FROM sku_fulfillment_history
                      WHERE mode='stocked'
                  )
                  AND NOT EXISTS(
                      SELECT 1 FROM store_fulfillment_history
                      WHERE mode='stocked'
                  )
                """
            )
            has_inventory = inventory_not_applicable or exists(
                """
                SELECT 1
                WHERE EXISTS(SELECT 1 FROM inventory_snapshots)
                   OR EXISTS(SELECT 1 FROM inventory_balances)
                """
            )
            has_exchange_rate = exists(
                "SELECT 1 FROM exchange_rates LIMIT 1"
            )
            selector_count = connection.execute(
                """
                SELECT COUNT(*) FROM selector_activations
                WHERE action IN (
                    'increase_budget', 'reduce_budget',
                    'adjust_roas_target', 'pause', 'resume'
                )
                """
            ).fetchone()[0]
            settings_rows = {
                row["key"]: row["value"]
                for row in connection.execute(
                    """
                    SELECT key, value FROM system_settings
                    WHERE key IN (
                        'shadow_reconciled', 'rollback_drilled',
                        'refund_source_configured',
                        'three_day_reconciled'
                    )
                    """
                )
            }
            reconciliation_rows = connection.execute(
                """
                SELECT data_date, MIN(CAST(accuracy AS REAL)) min_accuracy
                FROM reconciliation_days
                GROUP BY data_date
                ORDER BY data_date DESC LIMIT 3
                """
            ).fetchall()
            has_three_day_reconciliation = (
                len(reconciliation_rows) == 3
                and all(
                    float(row["min_accuracy"]) >= 0.99
                    for row in reconciliation_rows
                )
            )
        readiness = LaunchReadiness(
            ziniao_bridge=_ziniao_bridge_ready(),
            tiktok_campaign_validation=has_tiktok_campaign,
            shopee_campaign_validation=has_shopee_campaign,
            business_costs=has_business_costs,
            sku_mapping=has_sku_mapping,
            order_cost_coverage=has_order_cost_coverage,
            refund_source=(
                settings_rows.get("refund_source_configured") == "true"
                or has_order_status_source
            ),
            inventory_source=has_inventory,
            exchange_rate_source=has_exchange_rate,
            feishu_callback=settings.feishu_callback_ready,
            shadow_reconciliation=(
                settings_rows.get("shadow_reconciled") == "true"
            ),
            rollback_drill=(
                settings_rows.get("rollback_drilled") == "true"
            ),
            selector_activation=selector_count == 10,
            platform_api_oauth=settings.platform_api_oauth_ready,
            three_day_reconciliation=(
                has_three_day_reconciliation
            ),
            live_allowlist=(
                bool(settings.live_allowlist)
                and has_three_day_reconciliation
            ),
            production_publish=bool(settings.publish_ssh_target),
        )
        excluded = {"platform_api_oauth"}
        if not settings.tiktok_collection_enabled:
            excluded.add("tiktok_campaign_validation")
        if not settings.feishu_callback_secret:
            excluded.add("feishu_callback")
        if not settings.live_writes:
            excluded.update(
                {"rollback_drill", "selector_activation", "live_allowlist"}
            )
        excluded_codes = frozenset(excluded)
        items = build_launch_checklist(
            readiness,
            excluded_codes=excluded_codes,
        )
        if args.format == "json":
            print(
                json.dumps(
                    [
                        {
                            "code": item.code,
                            "description": item.description,
                            "optional": item.optional,
                        }
                        for item in items
                    ],
                    ensure_ascii=False,
                )
            )
        else:
            print(render_launch_checklist(items))
        return 0
    if args.command == "reconcile":
        database.migrate()
        service = ReconciliationService(database)
        if args.reconcile_command == "import":
            try:
                with args.file.open(encoding="utf-8-sig", newline="") as stream:
                    rows = tuple(csv.DictReader(stream))
                required = {"field", "expected", "actual", "category"}
                if not rows or any(
                    not required <= set(row) or not row["field"] for row in rows
                ):
                    raise ValueError(
                        "CSV requires field,expected,actual,category"
                    )
                expected = {row["field"]: row["expected"] for row in rows}
                actual = {row["field"]: row["actual"] for row in rows}
                categories = {row["field"]: row["category"] for row in rows}
                result = service.record_day(
                    platform=args.platform,
                    store=args.store,
                    data_date=args.date,
                    expected=expected,
                    actual=actual,
                    difference_categories=categories,
                )
            except (OSError, ValueError) as error:
                print(f"Reconciliation import rejected: {error}")
                return 2
            print(
                f"Reconciled {args.date.isoformat()} "
                f"accuracy={result.accuracy}"
            )
            return 0
        if args.reconcile_command == "report":
            rows = service.report(
                platform=args.platform,
                store=args.store,
                start=args.start,
                end=args.end,
            )
            for row in rows:
                print(
                    f"{row.data_date.isoformat()} accuracy={row.accuracy} "
                    f"differences={len(row.differences)}"
                )
            return 0
        parser.print_help()
        return 2
    if args.command == "strategy":
        database.migrate()
        if args.strategy_command == "replay":
            result = StrategyReplayService(database).replay(
                platform=args.platform,
                store=args.store,
                campaign_id=args.campaign,
                start=args.start,
                end=args.end,
            )
            print(
                f"strategy_replay={result.status} checked={result.checked} "
                f"mismatches={len(result.mismatches)}"
            )
            return 0 if result.status == "matched" else 2
        parser.print_help()
        return 2
    if args.command == "knowledge":
        database.migrate()
        read_model = KnowledgeReadModel(database)
        try:
            if args.knowledge_command == "search":
                results = read_model.search(args.query, limit=args.limit)
                if args.format == "json":
                    print(
                        json.dumps(
                            {"ok": True, "results": results},
                            ensure_ascii=False,
                        )
                    )
                else:
                    for item in results:
                        print(
                            f"{item['document_id']} "
                            f"{item.get('title_zh') or item['title_original']} "
                            f"{item['canonical_url']}"
                        )
                return 0
            if args.knowledge_command == "show":
                detail = read_model.document(args.document_id)
                print(
                    json.dumps(
                        {"ok": True, "document": detail},
                        ensure_ascii=False,
                    )
                    if args.format == "json"
                    else detail["content_original"]
                )
                return 0
            if args.knowledge_command == "status":
                print(
                    json.dumps(
                        {"ok": True, **read_model.status()},
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.knowledge_command in {"bootstrap", "update"}:
                if not settings.ziniao_shopee_store_id:
                    print("ZINIAO_SHOPEE_STORE_ID is required")
                    return 2
                service = KnowledgeService(
                    KnowledgeRepository(database),
                    ZiniaoCliClient(),
                    store_id=settings.ziniao_shopee_store_id,
                    store_ids={
                        "tiktok_shop_th_help": (
                            settings.ziniao_tiktok_store_id
                        ),
                    },
                    sources=DEFAULT_SOURCES,
                    request_delay_ms=settings.knowledge_request_delay_ms,
                    max_pages=settings.knowledge_max_pages_per_run,
                )
                summary = (
                    service.bootstrap()
                    if args.knowledge_command == "bootstrap"
                    else service.update()
                )
                print(
                    f"knowledge_status={summary.status} "
                    f"fetched={summary.fetched} new={summary.new} "
                    f"updated={summary.updated} failed={summary.failed} "
                    f"errors={','.join(summary.errors)}"
                )
                return 0
            if args.knowledge_command == "enrich":
                if not settings.openai_api_key:
                    print("OPENAI_API_KEY is required for enrichment")
                    return 2
                from adwatch.knowledge.enrichment import OpenAIEnricher
                from adwatch.knowledge.models import ExtractedDocument

                with database.connect() as connection:
                    pending = connection.execute(
                        """
                        SELECT v.id AS version_id, v.document_id,
                               v.title_original, v.content_original,
                               v.language, v.content_format,
                               d.canonical_url, d.document_type,
                               d.published_at, d.remote_updated_at
                        FROM knowledge_enrichments e
                        JOIN knowledge_document_versions v
                          ON v.id=e.version_id
                        JOIN knowledge_documents d
                          ON d.id=v.document_id
                        WHERE e.status='pending'
                        ORDER BY d.published_at DESC
                        """
                    ).fetchall()
                if not pending:
                    print("No pending enrichments found.")
                    return 0
                enricher = OpenAIEnricher(
                    api_key=settings.openai_api_key,
                    model=settings.knowledge_model,
                    enabled=settings.knowledge_enrichment,
                )
                enriched = 0
                skipped = 0
                failed = 0
                for row in pending:
                    doc = ExtractedDocument(
                        canonical_url=str(row["canonical_url"]),
                        document_type=str(row["document_type"]),
                        title_original=str(row["title_original"]),
                        language=str(row["language"]),
                        content_original=str(row["content_original"]),
                        published_at=str(row["published_at"] or ""),
                        remote_updated_at=str(row["remote_updated_at"] or ""),
                        metadata={},
                        content_format=str(row["content_format"]),
                    )
                    try:
                        result = enricher.enrich(doc)
                        now = datetime.now(UTC).isoformat()
                        with database.transaction() as connection:
                            connection.execute(
                                """
                                UPDATE knowledge_enrichments
                                SET status=?, title_zh=?, summary_zh=?,
                                    category=?, tags_json=?,
                                    key_rules_json=?, model=?,
                                    response_id=?, updated_at=?
                                WHERE version_id=?
                                """,
                                (
                                    result.status,
                                    result.title_zh,
                                    result.summary_zh,
                                    result.category,
                                    json.dumps(list(result.tags)),
                                    json.dumps(list(result.key_rules)),
                                    result.model,
                                    result.response_id,
                                    now,
                                    row["version_id"],
                                ),
                            )
                        if result.status == "completed":
                            enriched += 1
                        else:
                            skipped += 1
                    except Exception as error:  # noqa: BLE001 - 每篇知识文档独立隔离
                        failed += 1
                        now = datetime.now(UTC).isoformat()
                        with database.transaction() as connection:
                            connection.execute(
                                """
                                UPDATE knowledge_enrichments
                                SET status='failed', error_code=?,
                                    error_message=?, updated_at=?
                                WHERE version_id=?
                                """,
                                (
                                    type(error).__name__,
                                    str(error)[:500],
                                    now,
                                    row["version_id"],
                                ),
                            )
                    print(
                        f"  [{row['document_id'][:8]}] "
                        f"{result.title_zh[:50] if result.status == 'completed' else result.status}"
                    )
                print(
                    f"enrichment: enriched={enriched} skipped={skipped} "
                    f"failed={failed}"
                )
                return 0
            if args.knowledge_command == "reindex":
                with database.transaction() as connection:
                    connection.execute("DELETE FROM knowledge_fts")
                    rows = connection.execute(
                        """
                        SELECT v.id, v.document_id, v.title_original,
                               v.content_original
                        FROM knowledge_document_versions v
                        JOIN knowledge_documents d
                          ON d.current_version_id=v.id
                        """
                    ).fetchall()
                    for row in rows:
                        chunk_id = str(uuid.uuid4())
                        connection.execute(
                            """
                            INSERT INTO knowledge_fts(
                                chunk_id, document_id, version_id,
                                title_original, content_original
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                chunk_id,
                                row["document_id"],
                                row["id"],
                                row["title_original"],
                                row["content_original"],
                            ),
                        )
                print(f"reindex: {len(rows)} documents rebuilt")
                return 0
            print(
                "Knowledge command is not available in this build."
            )
            return 2
        except (KeyError, ValueError) as error:
            print(f"Knowledge query rejected: {error}")
            return 2
    if args.command == "approval":
        database.migrate()
        if args.approval_command == "serve":
            if not settings.feishu_callback_secret:
                print("FEISHU_CALLBACK_SECRET is required")
                return 2
            print(
                f"Approval callback: http://{args.host}:{args.port}"
            )
            serve_callback(
                database,
                secret=settings.feishu_callback_secret,
                host=args.host,
                port=args.port,
            )
            return 0
        parser.print_help()
        return 2
    if args.command == "activation":
        database.migrate()
        activation_store = SelectorActivationStore(database)
        if args.activation_command == "list":
            for item in activation_store.list():
                print(
                    f"{item.platform}/{item.action} "
                    f"version={item.selector_version} "
                    f"store={item.store_id} "
                    f"activated_at={item.activated_at}"
                )
            return 0
        if args.activation_command == "register":
            evidence = (args.evidence_before, args.evidence_after)
            if not all(path.is_file() for path in evidence):
                print("Activation evidence files must exist")
                return 2
            try:
                selectors = json.loads(
                    args.selectors_file.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                print(f"Selector configuration is invalid: {error}")
                return 2
            required_selectors = {"value", "stage", "submit"}
            if (
                not isinstance(selectors, dict)
                or not required_selectors <= selectors.keys()
                or not all(
                    isinstance(value, str) and value
                    for value in selectors.values()
                )
            ):
                print(
                    "Selector configuration must contain non-empty "
                    "value, stage and submit strings"
                )
                return 2
            activation_store.register(
                platform=args.platform,
                action=args.action,
                selector_version=args.version,
                selectors=selectors,
                store_id=args.store_id,
                activated_by=args.activated_by,
                evidence_before=str(args.evidence_before.resolve()),
                evidence_after=str(args.evidence_after.resolve()),
            )
            print(
                f"Activated {args.platform}/{args.action} "
                f"version={args.version}"
            )
            return 0
        parser.print_help()
        return 2
    if args.command == "execute":
        if args.execution_mode == "live" and not settings.live_writes:
            print("ADWATCH_LIVE_WRITES is disabled")
            return 2
        try:
            expected_before = json.loads(args.expected_before)
        except json.JSONDecodeError as error:
            print(f"--expected-before must be valid JSON: {error}")
            return 2
        if not isinstance(expected_before, dict):
            print("--expected-before must be a JSON object")
            return 2
        database.migrate()
        policy = ExecutionPolicy(
            live_writes=settings.live_writes,
            allowed_targets=settings.live_allowlist,
        )
        backend = ZiniaoExecutionBackend(
            ZiniaoCliClient(),
            mode=args.execution_mode,
            policy=policy,
            activations=SelectorActivationStore(database),
            screenshot_dir=settings.data_dir / "screenshots",
        )
        try:
            result = SafeExecutor(database, backend).execute(
                args.approval_id,
                idempotency_key=args.idempotency_key,
                expected_before=expected_before,
            )
        except (ExecutionError, PolicyError, OSError, ZiniaoApiError) as error:
            print(f"Execution rejected: {error}")
            return 2
        print(f"audit={result.audit_id} status={result.status}")
        return 0 if result.status in {"succeeded", "rolled_back"} else 2
    if args.command == "report":
        database.migrate()
        if args.report_command == "daily":
            markdown = render_daily_markdown(
                ReportReadModel(database).daily(args.date),
                simulated=False,
            )
            destination = (
                settings.report_dir / f"daily-{args.date.isoformat()}.md"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(markdown, encoding="utf-8")
            print(f"Daily report: {destination}")
            return 0
        if args.report_command == "monthly":
            try:
                year_text, month_text = args.month.split("-", 1)
                year, month_number = int(year_text), int(month_text)
            except (ValueError, IndexError):
                print("Month must use YYYY-MM format")
                return 2
            markdown = render_monthly_markdown(database, year, month_number)
            destination = settings.report_dir / f"monthly-{args.month}.md"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(markdown, encoding="utf-8")
            print(f"Monthly report: {destination}")
            return 0
        if args.report_command == "weekly":
            markdown = render_weekly_markdown(database, args.end)
            destination = (
                settings.report_dir / f"weekly-{args.end.isoformat()}.md"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(markdown, encoding="utf-8")
            print(f"Weekly report: {destination}")
            return 0
        parser.print_help()
        return 2
    if args.command == "backup":
        database.migrate()
        if args.backup_command == "create":
            destination = create_backup(database, args.output)
            integrity = verify_backup(destination)
            print(f"Backup: {destination} integrity={integrity}")
            return 0 if integrity == "ok" else 2
        if args.backup_command == "verify":
            integrity = verify_backup(args.path)
            if integrity != "ok":
                print(
                    f"Backup verification failed: "
                    f"{args.path} integrity={integrity}"
                )
                return 2
            print(f"Backup verified: {args.path} integrity=ok")
            return 0
        parser.print_help()
        return 2
    if args.command == "replicate":
        database.migrate()
        if args.replicate_command == "export":
            if args.start > args.end:
                print("replicate start date must not be after end date")
                return 2
            destination = export_facts_to_path(
                database,
                start=args.start,
                end=args.end,
                destination=args.output,
            )
            print(f"replicate_export=ok path={destination}")
            return 0
        if args.replicate_command == "import":
            payload = sys.stdin.buffer.read() if args.stdin else args.file.read_bytes()
            try:
                imported = import_facts(database, payload)
                operations = OperationsSyncService(database).sync()
                recalculation = AnalysisRecalculationService(database).run(
                    start=imported.start,
                    end=imported.end,
                    destination=(
                        settings.report_dir
                        / (
                            "replicated-recalculation-"
                            f"{imported.start.isoformat()}-"
                            f"{imported.end.isoformat()}.json"
                        )
                    ),
                )
            except (OSError, TypeError, ValueError) as error:
                print(f"replicate_import=failed error={error}")
                return 2
            print(
                f"replicate_import=ok rows={imported.rows} "
                f"metrics={recalculation.metrics_processed} "
                f"profits={recalculation.profit_results} "
                f"pending={recalculation.pending_data} "
                f"supplier_costed={operations.supplier_costed}"
            )
            return 0
        if args.replicate_command == "publish":
            target = args.target or settings.publish_ssh_target
            if not target:
                print("replicate_publish=failed error=SSH target is not configured")
                return 2
            try:
                payload = export_facts(
                    database, start=args.start, end=args.end
                )
                output = publish_facts(
                    payload,
                    target=target,
                    container=settings.publish_container,
                )
            except (OSError, RuntimeError, ValueError) as error:
                print(f"replicate_publish=failed error={error}")
                return 2
            print(f"replicate_publish=ok remote={output}")
            return 0
        parser.print_help()
        return 2
    if args.command == "seed-business-data":
        database.migrate()
        count = AnalysisService(database).seed_mock_business_data(args.date)
        print(f"Seeded business inputs for {count} metric rows")
        return 0
    if args.command == "analyze":
        database.migrate()
        has_range = args.start is not None or args.end is not None
        if args.date is not None and has_range:
            print("analyze requires either --date or --from/--to")
            return 2
        if (args.start is None) != (args.end is None):
            print("analyze range requires both --from and --to")
            return 2
        if args.output is not None and not has_range:
            print("analyze --output requires --from/--to")
            return 2
        if args.start is not None and args.start > args.end:
            print("analyze start date must not be after end date")
            return 2
        if args.start is not None:
            destination = args.output or (
                settings.report_dir
                / (
                    "profit-recalculation-"
                    f"{args.start.isoformat()}-{args.end.isoformat()}.json"
                )
            )
            result = AnalysisRecalculationService(database).run(
                start=args.start,
                end=args.end,
                destination=destination,
            )
            profit_diff = result.differences["profit_results"]
            lineage_diff = result.differences["input_lineage"]
            print(
                f"days={result.days_processed} "
                f"metrics={result.metrics_processed} "
                f"profit_results={result.profit_results} "
                f"pending_data={result.pending_data} "
                f"profit_added={profit_diff['added']} "
                f"profit_changed={profit_diff['changed']} "
                f"profit_unchanged={profit_diff['unchanged']} "
                f"profit_removed={profit_diff['removed']} "
                f"lineage_changed={lineage_diff['changed']} "
                f"report={destination}"
            )
            return 0
        data_date = args.date or datetime.now().astimezone().date()
        summary = AnalysisService(database).run(data_date)
        print(
            f"metrics={summary.metrics_processed} "
            f"profit_results={summary.profit_results} "
            f"alerts={summary.alerts} "
            f"recommendations={summary.recommendations} "
            f"circuit_open={str(summary.circuit_open).lower()}"
        )
        return 0
    if args.command == "business":
        database.migrate()
        try:
            if args.business_command == "export-template":
                count = export_business_template(
                    database,
                    start=args.start,
                    end=args.end,
                    destination=args.output,
                )
                print(f"Exported {count} business input rows: {args.output}")
                return 0
            if args.business_command == "import":
                count = import_business_inputs(database, args.file)
                print(f"Imported {count} business input rows")
                return 0
            if args.business_command == "import-minimal":
                count = import_minimal_business_inputs(database, args.file)
                print(f"Imported {count} minimal business input rows")
                return 0
            if args.business_command == "import-orders":
                summary = import_order_costs(database, args.file)
                print(
                    "Imported order costs: "
                    f"read={summary.read} "
                    f"inserted={summary.inserted} "
                    f"updated={summary.updated} "
                    f"deduplicated={summary.deduplicated}"
                )
                print(
                    f"date_range={summary.start.isoformat()}.."
                    f"{summary.end.isoformat()} "
                    f"total_cost_cny={summary.total_cost_cny:.2f}"
                )
                return 0
            if args.business_command == "import-shopee-orders":
                parsed = parse_order_export(
                    args.file,
                    store=args.store,
                    source_updated_at=datetime.fromtimestamp(
                        args.file.stat().st_mtime, UTC
                    ),
                )
                orders = OrderRepository(database)
                order_count = orders.upsert_orders(parsed.orders)
                sku_count = orders.upsert_skus(parsed.skus)
                print(
                    "Imported Shopee orders: "
                    f"orders={order_count} skus={sku_count} "
                    f"rejected={len(parsed.rejected)}"
                )
                return 0
            if args.business_command == "map-store":
                map_store(
                    database,
                    args.platform,
                    args.source,
                    args.target,
                )
                print(
                    f"Mapped store: {args.platform.lower()} "
                    f"{args.source} -> {args.target}"
                )
                return 0
            if args.business_command == "order-summary":
                for row in order_cost_summary(
                    database, args.start, args.end
                ):
                    print(
                        f"{row.order_date.isoformat()} {row.platform} "
                        f"{row.store} -> {row.canonical_store} "
                        f"orders={row.orders} units={row.units} "
                        f"total_cost_cny={row.total_cost_cny:.2f}"
                    )
                return 0
            if args.business_command == "export-pending-sku-costs":
                count = export_pending_sku_costs(database, args.output)
                print(
                    f"Exported {count} pending SKU costs: {args.output}"
                )
                return 0
            if args.business_command == "import-sku-costs":
                count = import_sku_costs(database, args.file)
                print(f"Imported {count} SKU costs")
                return 0
            if args.business_command == "sync-orders":
                result = OperationsSyncService(database).sync()
                print(
                    f"shipped={result.shipped} "
                    f"supplier_costed={result.supplier_costed} "
                    f"returned={result.returned} "
                    f"cancelled={result.cancelled} "
                    f"pending_cost={result.pending_cost} "
                    f"pending_fulfillment={result.pending_fulfillment} "
                    f"pending_inventory={result.pending_inventory} "
                    f"pending_order_identity={result.pending_order_identity} "
                    f"unchanged={result.unchanged}"
                )
                return (
                    2
                    if (
                        result.pending_cost
                        or result.pending_fulfillment
                        or result.pending_inventory
                        or result.pending_order_identity
                    )
                    else 0
                )
            if args.business_command == "preview-accounting-excel":
                preview = ExcelPrototypePreview(database).preview(args.file)
                payload = {
                    "sheet_names": preview.sheet_names,
                    "counts": preview.counts,
                    "rows": [
                        {
                            "sheet": row.sheet,
                            "row_number": row.row_number,
                            "status": row.status,
                            "natural_key": row.natural_key,
                            "message": row.message,
                        }
                        for row in preview.rows
                    ],
                }
                if args.format == "json":
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    print(
                        "Accounting Excel preview: "
                        + " ".join(
                            f"{key}={value}"
                            for key, value in preview.counts.items()
                        )
                    )
                return 0
            if args.business_command == "set-fulfillment":
                FulfillmentService(database).set_policy(
                    platform=args.platform,
                    store=args.store,
                    seller_sku=args.sku,
                    effective_date=args.effective_date,
                    mode=args.mode,
                    supply_status=args.supply_status,
                    note=args.note,
                )
                print(
                    f"fulfillment={args.platform.lower()}:{args.store}:"
                    f"{args.sku} mode={args.mode} "
                    f"effective={args.effective_date.isoformat()}"
                )
                return 0
            if args.business_command == "set-store-fulfillment-default":
                FulfillmentService(database).set_store_default(
                    platform=args.platform,
                    store=args.store,
                    effective_date=args.effective_date,
                    mode=args.mode,
                    supply_status=args.supply_status,
                    note=args.note,
                )
                print(
                    f"store_fulfillment={args.platform.lower()}:"
                    f"{args.store} mode={args.mode} "
                    f"effective={args.effective_date.isoformat()}"
                )
                return 0
            if args.business_command == "sync-exchange-rates":
                try:
                    count = sync_exchange_rates(
                        database,
                        EcbExchangeRateSource(),
                        currency=args.currency,
                        start=args.start,
                        end=args.end,
                    )
                except (OSError, ValueError) as error:
                    print(f"Exchange-rate sync failed: {error}")
                    return 2
                print(
                    f"Synced {count} {args.currency.upper()}/CNY "
                    "exchange rates from ECB"
                )
                return 0
            if (
                args.business_command
                == "mark-current-skus-supplier-fulfilled"
            ):
                count = FulfillmentService(
                    database
                ).mark_current_skus_supplier_fulfilled(
                    platform=args.platform,
                    store=args.store,
                    note=args.note,
                )
                print(f"marked_supplier_fulfilled={count}")
                return 0
        except BusinessInputError as error:
            print(f"Business input rejected: {error}")
            return 2
        parser.print_help()
        return 2
    if args.command == "schedule":
        if args.print_launchd:
            print(render_launchd_plist(Path.cwd(), settings))
            return 0
        if args.schedule_command is None:
            parser.print_help()
            return 2
        manager = ScheduleManager(
            project_dir=Path.cwd(),
            settings=settings,
        )
        try:
            if args.schedule_command == "install":
                status = manager.install(include_weekly=True)
                print(
                    f"schedule=installed "
                    f"healthy={str(status.healthy).lower()} "
                    f"project={status.project_dir} "
                    f"time={settings.daily_run_time}"
                )
            elif args.schedule_command == "repair":
                status = manager.repair()
                print(
                    f"schedule=repaired "
                    f"healthy={str(status.healthy).lower()} "
                    f"project={status.project_dir} "
                    f"time={settings.daily_run_time}"
                )
            elif args.schedule_command == "uninstall":
                status = manager.uninstall()
                unrecognized = "unrecognized_plist" in status.issues
                print(
                    f"schedule=uninstalled "
                    f"success={str(not unrecognized).lower()}"
                )
                return 2 if unrecognized else 0
            else:
                status = manager.status()
                health = "healthy" if status.healthy else "unhealthy"
                print(
                    f"schedule={health} "
                    f"loaded={str(status.loaded).lower()} "
                    f"enabled={str(status.enabled).lower()}"
                )
                if status.issues:
                    print(f"issues={','.join(status.issues)}")
            return 0 if status.healthy else 2
        except ScheduleCommandError as error:
            print(f"schedule=failed error={error}")
            return 2
    if args.command == "dashboard":
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
            print("Remote dashboard binding requires --allow-remote")
            return 2
        database.migrate()
        dashboard_date = (
            args.date
            or ReportReadModel(database).latest_data_date()
            or datetime.now().astimezone().date()
        )
        print(f"Dashboard: http://{args.host}:{args.port}")
        serve(
            database,
            host=args.host,
            port=args.port,
            default_date=dashboard_date,
            simulated=None,
            evidence_root=settings.evidence_dir,
        )
        return 0
    if args.command == "run" and args.workflow == "daily":
        execution_date = datetime.now(
            ZoneInfo(settings.timezone)
        ).date()
        report_date = args.date or execution_date - timedelta(days=1)
        result = DailyRunService.from_settings(
            settings,
            database=database,
        ).run(
            execution_date=execution_date,
            report_date=report_date,
            mode=args.mode,
            notify=not args.no_notify,
        )
        for message in result.messages:
            print(message)
        print(result.summary_line())
        return result.exit_code
    if args.command == "run" and args.workflow == "scheduled-daily":
        return run_scheduled_daily(settings)
    if args.command == "run" and args.workflow == "scheduled-weekly":
        return run_scheduled_weekly(settings)
    if args.command == "collect":
        database.migrate()
        if args.mode == "mock":
            collectors = [
                MockCollector(Platform.TIKTOK),
                MockCollector(Platform.SHOPEE),
            ]
        else:
            if not settings.ziniao_cli_ready:
                print("已启用的紫鸟平台店铺 ID 未配置。")
                return 2
            collectors = [
                ZiniaoCollector(settings, Platform(platform))
                for platform in settings.enabled_ziniao_platforms
            ]
        runner = PipelineRunner(database)
        try:
            summaries = [
                runner.run(collector, args.date) for collector in collectors
            ]
        except ZiniaoNotConfigured as error:
            print(str(error))
            return 2
        report_path = write_quality_report(
            settings.report_dir,
            args.date,
            args.mode,
            summaries,
        )
        for summary in summaries:
            print(
                f"{summary.platform}: received={summary.received} "
                f"accepted={summary.accepted} "
                f"quarantined={summary.quarantined}"
            )
        print(f"Database: {settings.database_path}")
        print(f"Quality report: {report_path}")
    return 0


def entrypoint() -> None:
    raise SystemExit(main())


def _ziniao_bridge_ready() -> bool:
    try:
        ZiniaoCliClient().get_store_list()
        return True
    except (OSError, ZiniaoApiError):
        return False

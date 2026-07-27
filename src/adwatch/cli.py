import argparse
import calendar
import json
import shutil
from datetime import date, timedelta
from html import escape
from pathlib import Path

from adwatch.analytics.business_inputs import (
    BusinessInputError,
    export_business_template,
    import_business_inputs,
    import_minimal_business_inputs,
)
from adwatch.analytics.service import AnalysisService
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
from adwatch.operations.backup import create_backup, verify_backup
from adwatch.operations.launch_checklist import (
    LaunchReadiness,
    build_launch_checklist,
    render_launch_checklist,
)
from adwatch.operations.readiness import readiness_status
from adwatch.pipeline.runner import PipelineRunner
from adwatch.reporting.delivery import deliver_report
from adwatch.reporting.markdown import (
    render_daily_markdown,
    render_monthly_markdown,
    render_weekly_markdown,
)
from adwatch.reporting.quality import write_quality_report
from adwatch.reporting.read_model import ReportReadModel
from adwatch.storage.db import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adwatch")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("init")
    collect = subcommands.add_parser("collect")
    collect.add_argument("--mode", choices=("mock", "ziniao"), default="mock")
    collect.add_argument("--date", type=date.fromisoformat, default=date.today())
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
    seed.add_argument("--date", type=date.fromisoformat, default=date.today())
    analyze = subcommands.add_parser("analyze")
    analyze.add_argument("--date", type=date.fromisoformat, default=date.today())
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
    run = subcommands.add_parser("run")
    workflows = run.add_subparsers(dest="workflow")
    daily = workflows.add_parser("daily")
    daily.add_argument("--mode", choices=("mock", "ziniao"), default="mock")
    daily.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today() - timedelta(days=1),
    )
    schedule = subcommands.add_parser("schedule")
    schedule.add_argument("--print-launchd", action="store_true")
    dashboard = subcommands.add_parser("dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--date", type=date.fromisoformat, default=date.today())
    dashboard.add_argument("--allow-remote", action="store_true")
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
                    "SELECT 1 FROM product_costs LIMIT 1"
                ).fetchone()
            )
        checks = readiness_status(
            bridge_ready=_ziniao_bridge_ready(),
            has_tiktok_campaign=has_tiktok_campaign,
            has_business_costs=has_business_costs,
            feishu_callback_ready=settings.feishu_callback_ready,
        )
        for check in checks:
            print(f"{check.name}={check.status}")
        return 0 if all(item.status == "ready" for item in checks) else 2
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
                "SELECT 1 FROM product_costs LIMIT 1"
            )
            has_sku_mapping = exists(
                """
                SELECT 1 FROM sku_mappings
                WHERE tiktok_product_id IS NOT NULL
                   OR shopee_product_id IS NOT NULL
                LIMIT 1
                """
            )
            has_inventory = exists(
                "SELECT 1 FROM inventory_snapshots LIMIT 1"
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
        readiness = LaunchReadiness(
            ziniao_bridge=_ziniao_bridge_ready(),
            tiktok_campaign_validation=has_tiktok_campaign,
            shopee_campaign_validation=has_shopee_campaign,
            business_costs=has_business_costs,
            sku_mapping=has_sku_mapping,
            refund_source=(
                settings_rows.get("refund_source_configured") == "true"
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
                settings_rows.get("three_day_reconciled") == "true"
            ),
            live_allowlist=bool(settings.live_allowlist),
        )
        items = build_launch_checklist(readiness)
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
                days = calendar.monthrange(year, month_number)[1]
            except (ValueError, IndexError):
                print("Month must use YYYY-MM format")
                return 2
            read_model = ReportReadModel(database)
            snapshots = [
                read_model.daily(date(year, month_number, day))
                for day in range(1, days + 1)
            ]
            snapshots = [item for item in snapshots if item.platforms]
            markdown = render_monthly_markdown(
                snapshots, month=args.month
            )
            destination = settings.report_dir / f"monthly-{args.month}.md"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(markdown, encoding="utf-8")
            print(f"Monthly report: {destination}")
            return 0
        if args.report_command == "weekly":
            read_model = ReportReadModel(database)
            snapshots = [
                read_model.daily(args.end - timedelta(days=offset))
                for offset in range(6, -1, -1)
            ]
            snapshots = [item for item in snapshots if item.platforms]
            markdown = render_weekly_markdown(snapshots)
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
    if args.command == "seed-business-data":
        database.migrate()
        count = AnalysisService(database).seed_mock_business_data(args.date)
        print(f"Seeded business inputs for {count} metric rows")
        return 0
    if args.command == "analyze":
        database.migrate()
        summary = AnalysisService(database).run(args.date)
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
        except BusinessInputError as error:
            print(f"Business input rejected: {error}")
            return 2
        parser.print_help()
        return 2
    if args.command == "schedule":
        if args.print_launchd:
            print(render_launchd_plist(Path.cwd()))
        return 0
    if args.command == "dashboard":
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
            print("Remote dashboard binding requires --allow-remote")
            return 2
        database.migrate()
        print(f"Dashboard: http://{args.host}:{args.port}")
        serve(
            database,
            host=args.host,
            port=args.port,
            default_date=args.date,
            simulated=True,
        )
        return 0
    if args.command == "run" and args.workflow == "daily":
        database.migrate()
        if args.mode == "ziniao" and not settings.ziniao_cli_ready:
            print(
                "Ziniao store IDs are not configured. Set "
                "ZINIAO_TIKTOK_STORE_ID and ZINIAO_SHOPEE_STORE_ID."
            )
            return 2
        collector_type = (
            MockCollector if args.mode == "mock" else ZiniaoCollector
        )
        runner = PipelineRunner(database)
        summaries = []
        collection_errors = []
        for platform in (Platform.TIKTOK, Platform.SHOPEE):
            collector = (
                collector_type(platform)
                if args.mode == "mock"
                else collector_type(settings, platform)
            )
            try:
                summaries.append(runner.run(collector, args.date))
            except Exception as error:
                collection_errors.append((platform.value, error))
                print(
                    f"{platform.value} collection failed: "
                    f"{type(error).__name__}: {error}"
                )
        write_quality_report(
            settings.report_dir, args.date, args.mode, summaries
        )
        analysis = AnalysisService(database)
        if args.mode == "mock":
            analysis.seed_mock_business_data(args.date)
        analysis_summary = analysis.run(args.date)
        markdown = render_daily_markdown(
            ReportReadModel(database).daily(args.date),
            simulated=args.mode == "mock",
        )
        delivery = deliver_report(
            markdown,
            data_date=args.date,
            report_dir=settings.report_dir,
            webhook_url=settings.feishu_webhook,
        )
        run_status = "partial" if collection_errors else "ok"
        print(
            f"daily_run={run_status} metrics={analysis_summary.metrics_processed} "
            f"recommendations={analysis_summary.recommendations} "
            f"delivery={delivery.status} report={delivery.path}"
        )
        return 2 if collection_errors else 0
    if args.command == "collect":
        database.migrate()
        if args.mode == "mock":
            collectors = [
                MockCollector(Platform.TIKTOK),
                MockCollector(Platform.SHOPEE),
            ]
        else:
            if not settings.ziniao_cli_ready:
                print(
                    "Ziniao store IDs are not configured. Set "
                    "ZINIAO_TIKTOK_STORE_ID and ZINIAO_SHOPEE_STORE_ID."
                )
                return 2
            collectors = [
                ZiniaoCollector(settings, Platform.TIKTOK),
                ZiniaoCollector(settings, Platform.SHOPEE),
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


def render_launchd_plist(project_dir: Path) -> str:
    executable = project_dir / ".venv" / "bin" / "adwatch"
    stdout = project_dir / "var" / "logs" / "launchd.out.log"
    stderr = project_dir / "var" / "logs" / "launchd.err.log"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        "<key>Label</key><string>com.adwatch.daily</string>"
        "<key>ProgramArguments</key><array>"
        f"<string>{escape(str(executable))}</string>"
        "<string>run</string><string>daily</string>"
        "<string>--mode</string><string>ziniao</string></array>"
        f"<key>WorkingDirectory</key><string>{escape(str(project_dir))}</string>"
        "<key>EnvironmentVariables</key><dict>"
        "<key>PATH</key>"
        "<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>"
        "</dict>"
        f"<key>StandardOutPath</key><string>{escape(str(stdout))}</string>"
        f"<key>StandardErrorPath</key><string>{escape(str(stderr))}</string>"
        "<key>StartCalendarInterval</key><dict>"
        "<key>Hour</key><integer>8</integer>"
        "<key>Minute</key><integer>0</integer>"
        "</dict><key>RunAtLoad</key><false/>"
        "</dict></plist>"
    )

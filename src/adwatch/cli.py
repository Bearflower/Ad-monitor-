import argparse
import shutil
from datetime import date, timedelta
from html import escape
from pathlib import Path

from adwatch.analytics.business_inputs import (
    BusinessInputError,
    export_business_template,
    import_business_inputs,
)
from adwatch.analytics.service import AnalysisService
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
from adwatch.pipeline.runner import PipelineRunner
from adwatch.reporting.delivery import deliver_report
from adwatch.reporting.markdown import render_daily_markdown
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
        try:
            summaries = [
                runner.run(
                    (
                        collector_type(platform)
                        if args.mode == "mock"
                        else collector_type(settings, platform)
                    ),
                    args.date,
                )
                for platform in (Platform.TIKTOK, Platform.SHOPEE)
            ]
        except ZiniaoNotConfigured as error:
            print(str(error))
            return 2
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
        print(
            f"daily_run=ok metrics={analysis_summary.metrics_processed} "
            f"recommendations={analysis_summary.recommendations} "
            f"delivery={delivery.status} report={delivery.path}"
        )
        return 0
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
        "<key>Hour</key><integer>9</integer>"
        "<key>Minute</key><integer>0</integer>"
        "</dict><key>RunAtLoad</key><false/>"
        "</dict></plist>"
    )

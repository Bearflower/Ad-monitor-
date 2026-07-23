import argparse
from datetime import date

from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.collectors.ziniao import ZiniaoCollector, ZiniaoNotConfigured
from adwatch.config import Settings
from adwatch.domain import Platform
from adwatch.dashboard.app import serve
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
    run = subcommands.add_parser("run")
    workflows = run.add_subparsers(dest="workflow")
    daily = workflows.add_parser("daily")
    daily.add_argument("--mode", choices=("mock", "ziniao"), default="mock")
    daily.add_argument("--date", type=date.fromisoformat, default=date.today())
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
        print(f"Ziniao configured: {'yes' if settings.ziniao_ready else 'no'}")
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
    if args.command == "schedule":
        if args.print_launchd:
            print(
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                "<plist version=\"1.0\"><dict>"
                "<key>Label</key><string>com.adwatch.daily</string>"
                "<key>ProgramArguments</key><array>"
                "<string>adwatch</string><string>run</string>"
                "<string>daily</string></array>"
                "<key>StartCalendarInterval</key><dict>"
                "<key>Hour</key><integer>8</integer>"
                "<key>Minute</key><integer>0</integer>"
                "</dict></dict></plist>"
            )
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
        if args.mode != "mock":
            print("Daily Ziniao run requires the real collector integration")
            return 2
        runner = PipelineRunner(database)
        summaries = [
            runner.run(MockCollector(platform), args.date)
            for platform in (Platform.TIKTOK, Platform.SHOPEE)
        ]
        write_quality_report(
            settings.report_dir, args.date, args.mode, summaries
        )
        analysis = AnalysisService(database)
        analysis.seed_mock_business_data(args.date)
        analysis_summary = analysis.run(args.date)
        markdown = render_daily_markdown(
            ReportReadModel(database).daily(args.date), simulated=True
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
            if not settings.ziniao_ready:
                print(
                    "Ziniao is not configured. Set ZINIAO_COMPANY, "
                    "ZINIAO_USERNAME, ZINIAO_PASSWORD and ZINIAO_ENDPOINT."
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

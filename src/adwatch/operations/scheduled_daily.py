from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from adwatch.config import Settings
from adwatch.operations.daily_run import DailyRunService
from adwatch.operations.run_state import (
    RunLockUnavailable,
    RunStateStore,
)
from adwatch.reporting.delivery import (
    build_report_title,
    deliver_report,
)
from adwatch.reporting.markdown import (
    render_monthly_markdown,
    render_weekly_markdown,
)
from adwatch.storage.db import Database


@dataclass(frozen=True)
class ScheduleDecision:
    should_run: bool
    report_date: date
    reason: str


def decide_scheduled_run(
    *,
    now: datetime,
    run_time: time,
    last_successful_report_date: date | None,
) -> ScheduleDecision:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduled run time must be timezone-aware")
    report_date = now.date() - timedelta(days=1)
    current_time = now.time().replace(tzinfo=None)
    if current_time < run_time:
        return ScheduleDecision(False, report_date, "before_run_time")
    if last_successful_report_date == report_date:
        return ScheduleDecision(False, report_date, "already_successful")
    return ScheduleDecision(True, report_date, "report_missing")


def run_scheduled_daily(
    settings: Settings,
    *,
    now: datetime | None = None,
    daily_service: DailyRunService | None = None,
) -> int:
    current = now or datetime.now(ZoneInfo(settings.timezone))
    store = RunStateStore(settings.data_dir)
    run_time = time(settings.daily_run_hour, settings.daily_run_minute)
    decision = decide_scheduled_run(
        now=current,
        run_time=run_time,
        last_successful_report_date=(
            store.last_successful_report_date()
        ),
    )
    if not decision.should_run:
        store.record_noop(decision.reason, current, decision.report_date)
        print(
            f"scheduled_daily=noop "
            f"report_date={decision.report_date.isoformat()} "
            f"reason={decision.reason}"
        )
        return 0

    try:
        with store.lock():
            locked_decision = decide_scheduled_run(
                now=current,
                run_time=run_time,
                last_successful_report_date=(
                    store.last_successful_report_date()
                ),
            )
            if not locked_decision.should_run:
                store.record_noop(
                    locked_decision.reason,
                    current,
                    locked_decision.report_date,
                )
                print(
                    f"scheduled_daily=noop "
                    f"report_date={locked_decision.report_date.isoformat()} "
                    f"reason={locked_decision.reason}"
                )
                return 0
            service = daily_service or DailyRunService.from_settings(
                settings
            )
            result = service.run(
                execution_date=current.date(),
                report_date=locked_decision.report_date,
                mode="ziniao",
                notify=True,
            )
            store.record_result(
                current,
                locked_decision.report_date,
                result,
            )
            print(
                f"scheduled_daily={result.status} "
                f"report_date={locked_decision.report_date.isoformat()}"
            )
            return result.exit_code
    except RunLockUnavailable:
        store.record_noop("already_running", current, decision.report_date)
        print(
            f"scheduled_daily=noop "
            f"report_date={decision.report_date.isoformat()} "
            "reason=already_running"
        )
        return 0


def _is_last_week_of_month(week_end: date) -> bool:
    """判断指定日期所在周是否是月最后一周。

    通过检查下周的月份是否与当前月份不同来判断。
    """
    next_week = week_end + timedelta(days=7)
    return next_week.month != week_end.month


def run_scheduled_weekly(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """运行每周调度：生成并发送周报。

    每周一运行，获取上周日（week_end = 当前日期 - 1 天）的数据。
    如果上周日是月最后一周，额外生成月报。

    Args:
        settings: 应用配置
        now: 当前时间（用于测试注入），默认为系统当前时间

    Returns:
        0 = 成功或无需运行
        1 = 失败
    """
    current = now or datetime.now(ZoneInfo(settings.timezone))
    store = RunStateStore(settings.data_dir)
    week_end = current.date() - timedelta(days=1)  # 周日（周一跑上周数据）
    week_start = week_end - timedelta(days=6)

    # 幂等性检查：防止重复发送
    last_weekly = store.last_successful_weekly_date()
    if last_weekly == week_end:
        store.record_noop("weekly_already_successful", current, week_end)
        print(
            f"scheduled_weekly=noop "
            f"week_end={week_end.isoformat()} "
            "reason=weekly_already_successful"
        )
        return 0

    try:
        with store.weekly_lock():
            # 双重检查：确认未被其他进程处理
            last_weekly = store.last_successful_weekly_date()
            if last_weekly == week_end:
                store.record_noop(
                    "weekly_already_successful", current, week_end
                )
                return 0

            # 连接数据库
            database = Database(settings.database_path)

            # 生成周报
            markdown = render_weekly_markdown(database, week_end)

            # 交付周报
            title = build_report_title(
                "weekly",
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
            )
            result = deliver_report(
                markdown,
                data_date=week_end,
                report_dir=settings.report_dir,
                webhook_url=settings.feishu_webhook,
                title=title,
                filename=f"weekly-{week_end.isoformat()}.md",
            )

            # 记录运行状态
            store.record_weekly_result(current, week_end, result)
            print(
                f"scheduled_weekly={result.status} "
                f"week_end={week_end.isoformat()}"
            )

            # 如果是月最后一周，额外生成月报
            if _is_last_week_of_month(week_end):
                month = week_end.month
                year = week_end.year
                month_markdown = render_monthly_markdown(database, year, month)
                month_title = build_report_title(
                    "monthly", year=year, month=month,
                )
                month_first = date(year, month, 1)
                month_result = deliver_report(
                    month_markdown,
                    data_date=month_first,
                    report_dir=settings.report_dir,
                    webhook_url=settings.feishu_webhook,
                    title=month_title,
                    filename=f"monthly-{year}-{month:02d}.md",
                )
                store.record_monthly_result(current, month_first, month_result)
                print(
                    f"scheduled_monthly={month_result.status} "
                    f"month={year}-{month:02d}"
                )

            return 0
    except RunLockUnavailable:
        store.record_noop("already_running", current, week_end)
        print(
            f"scheduled_weekly=noop "
            f"week_end={week_end.isoformat()} "
            "reason=already_running"
        )
        return 0
    except Exception:
        logger.exception("周报生成失败")
        store.record_noop("weekly_error", current, week_end)
        return 1

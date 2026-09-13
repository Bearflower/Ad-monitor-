from __future__ import annotations

import os
import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import adwatch.optimizer.repository as repository_module
from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.models import (
    Action,
    AdMetric,
    Confidence,
    DataQuality,
    DataQualityStatus,
    Evaluation,
    EvaluationStatus,
    Evidence,
    Feedback,
    FeedbackStatus,
    MetricGrain,
    Recommendation,
    RecommendationSlot,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)
from adwatch.optimizer.report import ShopeeAdsReportService
from adwatch.optimizer.repository import OptimizerRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")
REPORT_DATE = date(2026, 9, 10)
DATA_DATE = date(2026, 9, 9)
DECISION_AT = datetime(2026, 9, 10, 9, tzinfo=SHANGHAI)


def _metric(campaign_id: str, **changes: object) -> AdMetric:
    values: dict[str, object] = {
        "store_id": "store-1",
        "campaign_id": campaign_id,
        "campaign_name": campaign_id,
        "item_id": "__ALL__",
        "data_date": DATA_DATE,
        "currency": "THB",
        "spend": Decimal("10.00"),
        "gmv": Decimal("50.00"),
        "orders": 2,
        "collected_at": DECISION_AT.astimezone(UTC),
        "source": "ziniao-cli",
        "grain": MetricGrain.CAMPAIGN,
    }
    values.update(changes)
    return AdMetric(**values)


def _window(start: date, end: date) -> WindowStats:
    return WindowStats(start, end, Decimal(30), Decimal(150), 6)


def _recommendation(
    campaign_id: str, action: Action, **changes: object
) -> Recommendation:
    numeric = action in {
        Action.INCREASE_BUDGET,
        Action.DECREASE_BUDGET,
        Action.INCREASE_TARGET_ROAS,
        Action.DECREASE_TARGET_ROAS,
    }
    increasing = action in {Action.INCREASE_BUDGET, Action.INCREASE_TARGET_ROAS}
    current = Decimal(100) if numeric else None
    proposed = Decimal(120) if increasing else Decimal(80) if numeric else None
    ratio = Decimal("0.20") if increasing else Decimal("-0.20") if numeric else None
    quality = DataQuality(
        DataQualityStatus.HEALTHY,
        DECISION_AT.astimezone(UTC),
        allow_aggressive_scale=True,
    )
    values: dict[str, object] = {
        "recommendation_id": f"rec-{campaign_id}-{action.value}",
        "store_id": "store-1",
        "campaign_id": campaign_id,
        "data_date": DATA_DATE,
        "action": action,
        "confidence": Confidence.HIGH,
        "priority": 1,
        "current_value": current,
        "proposed_value": proposed,
        "change_ratio": ratio,
        "evidence": (
            Evidence(
                "trend",
                "证据 | 一行\n伪造表格",
                _window(date(2026, 9, 7), DATA_DATE),
                _window(date(2026, 9, 3), DATA_DATE),
                quality,
                (("minimum_roas", "2.50"),),
            ),
        ),
        "success_condition": SuccessCondition(Decimal("2.50"), Decimal("0.05"), 3),
        "rollback_condition": RollbackCondition(Decimal("2.00"), 2),
        "expected_impact": Decimal("0.10"),
        "risk": Decimal("0.20"),
        "observation_days": 3,
        "rule_version": "policy-v1",
        "input_hash": f"input-{campaign_id}-{action.value}",
        "revision": 1,
        "generated_at": DECISION_AT.astimezone(UTC),
    }
    values.update(changes)
    return Recommendation(**values)


def _evaluation(
    recommendation: Recommendation,
    feedback: Feedback,
    status: EvaluationStatus,
    *,
    evaluated_at: datetime,
    input_hash: str,
) -> Evaluation:
    return Evaluation(
        evaluation_id=f"eval-{feedback.feedback_id}-{input_hash}",
        recommendation_id=recommendation.recommendation_id,
        status=status,
        baseline_start=date(2026, 9, 4),
        baseline_end=date(2026, 9, 6),
        observation_start=date(2026, 9, 7),
        observation_end=DATA_DATE,
        algorithm_version="evaluation-v1",
        evaluated_at=evaluated_at,
        baseline_stats=_window(date(2026, 9, 4), date(2026, 9, 6)),
        after_stats=_window(date(2026, 9, 7), DATA_DATE),
        recommendation_revision=recommendation.revision,
        feedback_id=feedback.feedback_id,
        applied_value=recommendation.proposed_value,
        input_dates=(date(2026, 9, 4), DATA_DATE),
        input_hash=input_hash,
    )


@pytest.fixture
def repository(tmp_path: Path) -> OptimizerRepository:
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    return OptimizerRepository(database)


def _record_current_collection(repository: OptimizerRepository) -> None:
    repository.record_collection_day(
        "store-1", DATA_DATE, "success", attempted_at=DECISION_AT.astimezone(UTC)
    )


def test_successful_expected_day_is_current_and_shows_today_action(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    repository.upsert_metric(_metric("fresh"))
    _record_current_collection(repository)
    repository.save_recommendation(_recommendation("fresh", Action.INCREASE_BUDGET))

    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "状态：CURRENT" in report
    action_section = report.split("## 今日最新建议", 1)[1].split(
        "## 结构调整待办", 1
    )[0]
    assert "increase_budget" in action_section


def test_report_contains_complete_manual_action_sections_in_required_order(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    metrics = (
        _metric(
            "pause",
            campaign_name="**[危险](x)** <tag> | 活动\n下一行",
            spend=Decimal(5),
            gmv=Decimal(0),
            orders=0,
        ),
        _metric("growth", spend=Decimal(10), gmv=Decimal(100), orders=4),
        _metric("protect", spend=Decimal(20), gmv=Decimal(40), orders=2),
        _metric("observe", spend=Decimal(0), gmv=Decimal(0), orders=0),
        _metric("structure", spend=Decimal(1), gmv=Decimal(10), orders=1),
    )
    for metric in metrics:
        repository.upsert_metric(metric)
    _record_current_collection(repository)
    recommendations = (
        _recommendation("observe", Action.HOLD),
        _recommendation("protect", Action.DECREASE_BUDGET),
        _recommendation("growth", Action.INCREASE_BUDGET),
        _recommendation("pause", Action.PAUSE),
        _recommendation(
            "structure",
            Action.CREATE_GROUP,
            slot=RecommendationSlot.CREATE_GROUP,
        ),
    )
    saved = [repository.save_recommendation(item) for item in recommendations]
    repository.save_feedback(
        "store-1",
        Feedback(
            "feedback-growth",
            saved[2].recommendation_id,
            FeedbackStatus.EXECUTED,
            None,
            DECISION_AT.astimezone(UTC),
            "已改 | 等待\n评估",
            saved[2].revision,
        ),
    )

    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "# Shopee 广告优化日报｜2026-09-10" in report
    assert "人工修改" in report
    assert "数据日期：2026-09-09" in report
    assert "采集状态：成功" in report
    assert "花费：36.00 THB" in report
    assert "广告成交额：150.00 THB" in report
    assert "ROAS：4.1667" in report
    assert "广告归因订单：7" in report
    assert "## 活动排行" in report
    assert "## 今日最新建议" in report
    assert "当前值 | 建议值 | 变动" in report
    assert "证据窗口" in report
    assert "数据质量" in report
    assert "成功条件" in report
    assert "回退条件" in report
    assert "## 结构调整待办" in report
    assert "## 已人工执行但待评估" in report
    assert "## 数据异常与缺失" in report
    action_section = report.split("## 今日最新建议", 1)[1].split("## 结构调整待办", 1)[
        0
    ]
    assert (
        action_section.index("pause")
        < action_section.index("growth")
        < action_section.index("protect")
        < action_section.index("observe")
    )
    assert r"\*\*\[危险\]\(x\)\*\* &lt;tag&gt; \| 活动 下一行" in report
    assert "证据 \\| 一行 伪造表格" in report
    assert "已改 \\| 等待 评估" in report
    assert "未定义（零花费）" in report
    structure_section = report.split("## 结构调整待办", 1)[1].split(
        "## 已人工执行但待评估", 1
    )[0]
    for label in (
        "置信度：high",
        "数据质量：healthy",
        "证据窗口：短窗 2026-09-07~2026-09-09，长窗 2026-09-03~2026-09-09",
        "观察天数：3",
        "风险：20.00%",
        "成功条件：ROAS ≥ 2.5000",
        "回退条件：ROAS < 2.0000 连续 2 天",
        "请人工修改",
        r"证据 \| 一行 伪造表格",
    ):
        assert label in structure_section


def test_report_replaces_placeholder_campaign_name_with_stable_id(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    repository.upsert_metric(_metric("placeholder-name", campaign_name="..."))
    _record_current_collection(repository)

    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "未获取活动名称（placeholder-name）" in report
    assert "| ... (placeholder-name) |" not in report


def test_report_uses_latest_visible_revision_per_slot_and_excludes_future_and_other_store(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    old = _metric(
        "campaign-1",
        spend=Decimal(10),
        gmv=Decimal(50),
        collected_at=DECISION_AT.astimezone(UTC),
    )
    repository.upsert_metric(old)
    repository.upsert_metric(
        replace(
            old,
            spend=Decimal(999),
            collected_at=datetime(2026, 9, 10, 10, tzinfo=SHANGHAI),
        )
    )
    _record_current_collection(repository)
    repository.save_recommendation(
        _recommendation(
            "campaign-1",
            Action.HOLD,
            generated_at=datetime(2026, 9, 10, 8, tzinfo=SHANGHAI),
            input_hash="old",
        )
    )
    visible = repository.save_recommendation(
        _recommendation(
            "campaign-1",
            Action.INCREASE_BUDGET,
            generated_at=datetime(2026, 9, 10, 8, 30, tzinfo=SHANGHAI),
            input_hash="visible",
        )
    )
    repository.save_recommendation(
        _recommendation(
            "campaign-1",
            Action.DECREASE_BUDGET,
            generated_at=datetime(2026, 9, 10, 10, tzinfo=SHANGHAI),
            input_hash="future",
        )
    )
    repository.upsert_metric(
        _metric(
            "secret", store_id="store-2", campaign_name="SECRET", spend=Decimal(500)
        )
    )
    repository.save_recommendation(
        _recommendation("secret", Action.PAUSE, store_id="store-2")
    )

    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "花费：10.00 THB" in report
    assert visible.action.value in report
    assert "decrease_budget" not in report
    assert "SECRET" not in report
    assert "999" not in report


def test_no_data_and_stale_reports_are_honest_and_do_not_present_old_actions_as_today(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    old_date = date(2026, 9, 8)
    repository.upsert_metric(
        _metric(
            "old",
            data_date=old_date,
            collected_at=datetime(2026, 9, 9, 9, tzinfo=SHANGHAI),
        )
    )
    repository.record_collection_day(
        "store-1", DATA_DATE, "no_data", attempted_at=DECISION_AT.astimezone(UTC)
    )
    repository.save_recommendation(
        _recommendation("old", Action.INCREASE_BUDGET, data_date=old_date)
    )

    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "状态：STALE / NO_DATA" in report
    assert "预期数据日期：2026-09-09" in report
    assert "实际数据日期：2026-09-08" in report
    assert "今日无可用新建议" in report
    assert "increase_budget" not in report


@pytest.mark.parametrize("collection_status", ["no_data", "failed", None])
def test_same_day_residual_metrics_do_not_unlock_current_actions(
    repository: OptimizerRepository,
    tmp_path: Path,
    collection_status: str | None,
) -> None:
    repository.upsert_metric(_metric("residual"))
    if collection_status is not None:
        repository.record_collection_day(
            "store-1",
            DATA_DATE,
            collection_status,
            attempted_at=DECISION_AT.astimezone(UTC),
        )
    repository.save_recommendation(_recommendation("residual", Action.INCREASE_BUDGET))
    repository.save_recommendation(
        _recommendation(
            "residual",
            Action.CREATE_GROUP,
            slot=RecommendationSlot.CREATE_GROUP,
        )
    )

    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "状态：STALE / UNVERIFIED" in report
    assert "今日无可用新建议" in report
    assert "increase_budget" not in report
    assert "create_group" not in report
    assert "同日残留指标仅供参考，未经截止时点成功采集确认" in report


def test_report_render_uses_one_consistent_read_snapshot_during_concurrent_replace(
    repository: OptimizerRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision_at = datetime(2099, 9, 10, 9, tzinfo=SHANGHAI)
    old = _metric("campaign-1", spend=Decimal(10), gmv=Decimal(50))
    new = replace(
        old,
        spend=Decimal(20),
        gmv=Decimal(100),
        collected_at=datetime(2026, 9, 10, 10, tzinfo=SHANGHAI),
    )
    repository.upsert_metric(old)
    repository.record_collection_day(
        "store-1", DATA_DATE, "success", attempted_at=DECISION_AT.astimezone(UTC)
    )
    begin_write = threading.Event()
    committed = threading.Event()

    def writer() -> None:
        begin_write.wait(timeout=5)
        repository.replace_store_day(
            "store-1", DATA_DATE, (new,), (), "success", note="concurrent"
        )
        committed.set()

    thread = threading.Thread(target=writer)
    thread.start()
    original = repository_module._collection_day_as_of_connection
    triggered = False

    def after_first_read(connection, store_id, data_date, cutoff):
        nonlocal triggered
        result = original(connection, store_id, data_date, cutoff)
        if not triggered:
            triggered = True
            begin_write.set()
            assert committed.wait(timeout=5)
        return result

    monkeypatch.setattr(
        repository_module, "_collection_day_as_of_connection", after_first_read
    )
    service = ShopeeAdsReportService(repository, tmp_path / "reports")

    first = service.render("store-1", REPORT_DATE, decision_at)
    thread.join(timeout=5)
    second = service.render("store-1", REPORT_DATE, decision_at)

    assert not thread.is_alive()
    assert "花费：10.00 THB" in first
    assert "花费：20.00 THB" in second


def test_report_replays_collection_status_visible_at_decision_time(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    old_date = date(2026, 9, 8)
    repository.upsert_metric(
        _metric(
            "old",
            data_date=old_date,
            collected_at=datetime(2026, 9, 9, 9, tzinfo=SHANGHAI),
        )
    )
    repository.record_collection_day(
        "store-1",
        DATA_DATE,
        "no_data",
        attempted_at=datetime(2026, 9, 10, 8, 59, 59, 900_000, tzinfo=SHANGHAI),
    )
    repository.upsert_metric(
        _metric(
            "new",
            collected_at=datetime(2026, 9, 10, 10, tzinfo=SHANGHAI),
        )
    )
    repository.record_collection_day(
        "store-1",
        DATA_DATE,
        "success",
        attempted_at=datetime(2026, 9, 10, 10, tzinfo=SHANGHAI),
    )

    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "采集状态：无数据" in report
    assert "状态：STALE / NO_DATA" in report
    assert "实际数据日期：2026-09-08" in report


def test_report_without_any_metrics_still_generates_explicit_no_data_report(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    report = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )

    assert "状态：NO_DATA" in report
    assert "实际数据日期：无" in report
    assert "今日无可用新建议" in report
    assert "没有可排行的活动" in report


def test_pending_section_uses_latest_evaluation_revision_visible_at_decision_time(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    repository.upsert_metric(_metric("pending"))
    _record_current_collection(repository)
    recommendation = repository.save_recommendation(
        _recommendation("pending", Action.INCREASE_BUDGET)
    )
    feedback = repository.save_feedback(
        "store-1",
        Feedback(
            "feedback-pending",
            recommendation.recommendation_id,
            FeedbackStatus.EXECUTED,
            None,
            datetime(2026, 9, 10, 8, tzinfo=SHANGHAI),
            recommendation_revision=recommendation.revision,
        ),
    )
    repository.save_evaluation(
        "store-1",
        _evaluation(
            recommendation,
            feedback,
            EvaluationStatus.PENDING,
            evaluated_at=datetime(2026, 9, 10, 8, 30, tzinfo=SHANGHAI),
            input_hash="pending",
        ),
    )
    repository.save_evaluation(
        "store-1",
        _evaluation(
            recommendation,
            feedback,
            EvaluationStatus.EFFECTIVE,
            evaluated_at=datetime(2026, 9, 10, 10, tzinfo=SHANGHAI),
            input_hash="future-complete",
        ),
    )

    before_completion = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1", REPORT_DATE, DECISION_AT
    )
    after_completion = ShopeeAdsReportService(repository, tmp_path / "reports").render(
        "store-1",
        REPORT_DATE,
        datetime(2026, 9, 10, 10, 30, tzinfo=SHANGHAI),
    )

    pending_before = before_completion.split("## 已人工执行但待评估", 1)[1].split(
        "## 数据异常与缺失", 1
    )[0]
    pending_after = after_completion.split("## 已人工执行但待评估", 1)[1].split(
        "## 数据异常与缺失", 1
    )[0]
    assert "暂无已人工执行且待评估的动作" not in pending_before
    assert "暂无已人工执行且待评估的动作" in pending_after


def test_atomic_write_uses_expected_path_and_replace_failure_preserves_existing_file(
    repository: OptimizerRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    target = reports_dir / "shopee-ads-2026-09-10.md"
    target.write_text("上一版", encoding="utf-8")
    service = ShopeeAdsReportService(repository, reports_dir)

    def fail_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        service.write("store-1", REPORT_DATE, DECISION_AT)

    assert target.read_text(encoding="utf-8") == "上一版"
    assert tuple(reports_dir.glob(".shopee-ads-2026-09-10.md.*.tmp")) == ()


def test_successful_atomic_write_is_deterministic_for_explicit_generation_time(
    repository: OptimizerRepository, tmp_path: Path
) -> None:
    service = ShopeeAdsReportService(repository, tmp_path / "reports")

    first = service.write("store-1", REPORT_DATE, DECISION_AT)
    first_content = first.read_text(encoding="utf-8")
    second = service.write("store-1", REPORT_DATE, DECISION_AT)

    assert first == tmp_path / "reports" / "shopee-ads-2026-09-10.md"
    assert second.read_text(encoding="utf-8") == first_content
    assert "生成时间：2026-09-10 09:00:00 Asia/Shanghai" in first_content


def test_default_report_directory_is_local_var_reports(
    repository: OptimizerRepository,
) -> None:
    service = ShopeeAdsReportService(repository)

    assert service.reports_dir == Path("var/reports")

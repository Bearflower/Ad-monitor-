from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from adwatch.config import Settings
from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.evaluator import EVALUATOR_VERSION, EvaluatorService
from adwatch.optimizer.models import (
    Action,
    AdMetric,
    Confidence,
    DataQuality,
    DataQualityStatus,
    Evaluation,
    EvaluationStatus,
    Evidence,
    FeedbackStatus,
    MetricGrain,
    Recommendation,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)
from adwatch.optimizer.repository import OptimizerRepository


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=UTC)


def _window(start: date, end: date) -> WindowStats:
    return WindowStats(start, end, Decimal(100), Decimal(250), 5)


def _recommendation(**changes: object) -> Recommendation:
    values: dict[str, object] = {
        "recommendation_id": "store-1:campaign-1:2026-09-08:increase_budget",
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "data_date": date(2026, 9, 8),
        "action": Action.INCREASE_BUDGET,
        "confidence": Confidence.HIGH,
        "priority": 2,
        "current_value": Decimal(100),
        "proposed_value": Decimal(110),
        "change_ratio": Decimal("0.10"),
        "evidence": (
            Evidence(
                "increase_budget",
                "达标扩量",
                _window(date(2026, 9, 6), date(2026, 9, 8)),
                _window(date(2026, 9, 2), date(2026, 9, 8)),
                DataQuality(DataQualityStatus.HEALTHY, _at(9)),
                (("profile_version", "1"),),
            ),
        ),
        "success_condition": SuccessCondition(Decimal(2), Decimal("0.05"), 3),
        "rollback_condition": RollbackCondition(Decimal("1.8"), 2),
        "expected_impact": Decimal("0.10"),
        "risk": Decimal("0.10"),
        "observation_days": 3,
        "rule_version": "shopee-policy/v1",
        "input_hash": "policy-input",
        "revision": 1,
        "generated_at": _at(9),
    }
    values.update(changes)
    return Recommendation(**values)


def _metric(
    day: int,
    *,
    spend: str,
    gmv: str,
    orders: int,
    grain: MetricGrain = MetricGrain.CAMPAIGN,
    item_id: str = "__ALL__",
    collected_day: int | None = None,
) -> AdMetric:
    return AdMetric(
        "store-1",
        "campaign-1",
        "秋季扩量",
        item_id,
        date(2026, 9, day),
        "THB",
        Decimal(spend),
        Decimal(gmv),
        orders,
        _at(collected_day or min(day + 1, 30)),
        "ziniao-cli",
        grain,
    )


@pytest.fixture
def repository(tmp_path: Path) -> OptimizerRepository:
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    return OptimizerRepository(database)


@pytest.fixture
def service(repository: OptimizerRepository, tmp_path: Path) -> EvaluatorService:
    return EvaluatorService(
        repository,
        Settings(
            data_dir=tmp_path,
            ziniao_shopee_store_id="store-1",
            evaluation_days=3,
            effectiveness_min_samples=2,
        ),
    )


@pytest.mark.parametrize("status", list(FeedbackStatus))
def test_all_feedback_states_persist_and_only_explicit_execution_is_evaluable(
    status: FeedbackStatus,
    service: EvaluatorService,
    repository: OptimizerRepository,
):
    recommendation = repository.save_recommendation(_recommendation())
    actual = Decimal(115) if status is FeedbackStatus.MODIFIED else None

    saved = service.record_feedback(
        store_id="store-1",
        recommendation_id=recommendation.recommendation_id,
        recommendation_revision=recommendation.revision,
        status=status,
        actual_value=actual,
        executed_at=_at(9, 10),
        note="页面已人工确认",
    )

    assert saved.status is status
    assert saved.recommendation_revision == recommendation.revision
    assert service.feedback_for("store-1", recommendation.recommendation_id, 1) == saved
    assert service.pending_feedback("store-1") == (
        (saved,) if status in {FeedbackStatus.EXECUTED, FeedbackStatus.MODIFIED} else ()
    )


def test_feedback_uses_exact_immutable_recommendation_revision_and_is_conflict_safe(
    service: EvaluatorService, repository: OptimizerRepository
):
    first = repository.save_recommendation(_recommendation())
    second = repository.save_recommendation(
        _recommendation(input_hash="changed", proposed_value=Decimal(120), change_ratio=Decimal("0.2"))
    )

    saved = service.record_feedback(
        "store-1",
        first.recommendation_id,
        first.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )
    same = service.record_feedback(
        "store-1",
        first.recommendation_id,
        first.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )

    assert saved == same
    assert saved.recommendation_revision == 1
    assert second.revision == 2
    with pytest.raises(ValueError, match="conflict"):
        service.record_feedback(
            "store-1",
            first.recommendation_id,
            first.revision,
            FeedbackStatus.REJECTED,
            executed_at=_at(9, 11),
        )


def test_feedback_rejects_invalid_value_time_status_and_cross_store(
    service: EvaluatorService, repository: OptimizerRepository
):
    recommendation = repository.save_recommendation(_recommendation())
    cases = (
        ({"status": FeedbackStatus.MODIFIED, "actual_value": None}, "actual_value"),
        ({"status": FeedbackStatus.MODIFIED, "actual_value": Decimal(90)}, "direction"),
        ({"status": FeedbackStatus.EXECUTED, "actual_value": Decimal(111)}, "actual_value"),
        ({"status": FeedbackStatus.REJECTED, "actual_value": Decimal(111)}, "actual_value"),
        ({"status": "made_up"}, "status"),
        (
            {
                "status": FeedbackStatus.EXECUTED,
                "executed_at": _at(9, 10).replace(tzinfo=None),
            },
            "timezone",
        ),
        ({"status": FeedbackStatus.EXECUTED, "executed_at": _at(9, 8)}, "generated"),
    )
    for index, (overrides, message) in enumerate(cases):
        values: dict[str, object] = {
            "store_id": "store-1",
            "recommendation_id": recommendation.recommendation_id,
            "recommendation_revision": 1,
            "status": FeedbackStatus.EXECUTED,
            "executed_at": _at(9, 10),
        }
        values.update(overrides)
        with pytest.raises((TypeError, ValueError), match=message):
            service.record_feedback(**values)

    with pytest.raises(ValueError, match="recommendation"):
        service.record_feedback(
            "store-2",
            recommendation.recommendation_id,
            1,
            FeedbackStatus.EXECUTED,
            executed_at=_at(9, 10),
        )


def test_observed_page_control_change_never_creates_feedback(
    service: EvaluatorService, repository: OptimizerRepository
):
    recommendation = repository.save_recommendation(_recommendation())

    service.observe_campaign_value(
        store_id="store-1",
        campaign_id="campaign-1",
        observed_budget=Decimal(115),
        observed_target_roas=Decimal(2),
        observed_at=_at(9, 10),
    )

    assert service.feedback_for("store-1", recommendation.recommendation_id, 1) is None


def _save_complete_windows(
    repository: OptimizerRepository,
    *,
    after_gmv: str = "120",
    after_spend: str = "40",
    after_orders: int = 3,
) -> None:
    for day in (6, 7, 8):
        repository.upsert_metric(_metric(day, spend="40", gmv="100", orders=2))
        repository.upsert_metric(
            _metric(
                day,
                spend="999",
                gmv="9999",
                orders=99,
                grain=MetricGrain.ITEM,
                item_id=f"item-{day}",
            )
        )
    for day in (10, 11, 12):
        repository.upsert_metric(
            _metric(day, spend=after_spend, gmv=after_gmv, orders=after_orders)
        )


def _executed_feedback(
    service: EvaluatorService,
    repository: OptimizerRepository,
    *,
    status: FeedbackStatus = FeedbackStatus.EXECUTED,
    actual_value: Decimal | None = None,
):
    recommendation = repository.save_recommendation(_recommendation())
    return service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        recommendation.revision,
        status,
        actual_value=actual_value,
        executed_at=_at(9, 10),
    )


def _historical_evaluation(
    recommendation: Recommendation,
    feedback_id: str,
    status: EvaluationStatus,
    evaluated_at: datetime,
) -> Evaluation:
    baseline = WindowStats(date(2026, 8, 1), date(2026, 8, 3), Decimal(100), Decimal(250), 5)
    after = WindowStats(date(2026, 8, 5), date(2026, 8, 7), Decimal(110), Decimal(300), 6)
    return Evaluation(
        evaluation_id=f"evaluation-{feedback_id}",
        recommendation_id=recommendation.recommendation_id,
        status=status,
        baseline_start=baseline.start_date,
        baseline_end=baseline.end_date,
        observation_start=after.start_date,
        observation_end=after.end_date,
        algorithm_version=EVALUATOR_VERSION,
        evaluated_at=evaluated_at,
        baseline_stats=baseline,
        after_stats=after,
        note="历史评估",
        recommendation_revision=recommendation.revision,
        feedback_id=feedback_id,
        applied_value=recommendation.proposed_value,
        input_dates=(),
        input_hash=f"hash-{feedback_id}-{status.value}",
        revision=1,
    )


def test_mature_evaluation_uses_calendar_windows_campaign_grain_and_frozen_inputs(
    service: EvaluatorService, repository: OptimizerRepository
):
    feedback = _executed_feedback(service, repository)
    _save_complete_windows(repository)

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(13)
    )

    assert result.status is EvaluationStatus.EFFECTIVE
    assert result.baseline_stats == WindowStats(
        date(2026, 9, 6), date(2026, 9, 8), Decimal(120), Decimal(300), 6
    )
    assert result.after_stats == WindowStats(
        date(2026, 9, 10), date(2026, 9, 12), Decimal(120), Decimal(360), 9
    )
    assert result.applied_value == Decimal(110)
    assert result.input_dates == tuple(
        date(2026, 9, day) for day in (6, 7, 8, 10, 11, 12)
    )
    assert result.algorithm_version == EVALUATOR_VERSION
    assert result.input_hash
    assert all(label in result.note for label in ("GMV", "花费", "ROAS", "订单"))


def test_modified_feedback_freezes_actual_value_for_evaluation(
    service: EvaluatorService, repository: OptimizerRepository
):
    feedback = _executed_feedback(
        service,
        repository,
        status=FeedbackStatus.MODIFIED,
        actual_value=Decimal(115),
    )
    _save_complete_windows(repository)

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(13)
    )

    assert result.applied_value == Decimal(115)


def test_window_not_mature_is_pending_and_missing_days_are_insufficient(
    service: EvaluatorService, repository: OptimizerRepository
):
    pending_feedback = _executed_feedback(service, repository)
    pending = service.evaluate_feedback(
        "store-1",
        pending_feedback.feedback_id,
        as_of=date(2026, 9, 11),
        evaluated_at=_at(12),
    )
    assert pending.status is EvaluationStatus.PENDING
    assert pending.input_dates == ()

    for day in (6, 7, 8):
        repository.upsert_metric(_metric(day, spend="40", gmv="100", orders=2))
    for day in (10, 11):
        repository.upsert_metric(_metric(day, spend="40", gmv="120", orders=3))
    repository.upsert_metric(
        _metric(12, spend="40", gmv="120", orders=3, collected_day=14)
    )
    insufficient = service.evaluate_feedback(
        "store-1",
        pending_feedback.feedback_id,
        as_of=date(2026, 9, 12),
        evaluated_at=_at(13),
    )
    assert insufficient.status is EvaluationStatus.INSUFFICIENT_DATA
    assert "缺失" in insufficient.note


def test_pending_evaluation_never_reads_facts_after_as_of_cutoff(
    service: EvaluatorService, repository: OptimizerRepository
):
    feedback = _executed_feedback(service, repository)
    _save_complete_windows(repository, after_gmv="120")

    first = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 10), _at(13)
    )
    repository.upsert_metric(
        _metric(11, spend="40", gmv="999", orders=9, collected_day=13)
    )
    replay = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 10), _at(13)
    )

    assert first.status is EvaluationStatus.PENDING
    assert first.input_dates == tuple(date(2026, 9, day) for day in (6, 7, 8, 10))
    assert replay.input_hash == first.input_hash


def test_degraded_frozen_quality_is_inconclusive_not_failure(
    service: EvaluatorService, repository: OptimizerRepository
):
    degraded = replace(
        _recommendation(),
        evidence=(
            replace(
                _recommendation().evidence[0],
                quality=DataQuality(
                    DataQualityStatus.DEGRADED,
                    _at(9),
                    note="总览对账异常",
                ),
            ),
        ),
    )
    recommendation = repository.save_recommendation(degraded)
    feedback = service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        recommendation.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )
    _save_complete_windows(repository)

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 12), _at(13)
    )

    assert result.status is EvaluationStatus.INSUFFICIENT_DATA
    assert "质量" in result.note


def test_roas_floor_and_rollback_streak_override_gmv_growth(
    service: EvaluatorService, repository: OptimizerRepository
):
    feedback = _executed_feedback(service, repository)
    _save_complete_windows(
        repository, after_spend="100", after_gmv="170", after_orders=5
    )

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(13)
    )

    assert result.after_stats.gmv > result.baseline_stats.gmv
    assert result.status is EvaluationStatus.NEGATIVE
    assert "ROAS" in result.note


def test_change_below_frozen_success_threshold_is_no_change(
    service: EvaluatorService, repository: OptimizerRepository
):
    feedback = _executed_feedback(service, repository)
    _save_complete_windows(repository, after_gmv="103", after_spend="40")

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(13)
    )

    assert result.status is EvaluationStatus.NO_CHANGE


@pytest.mark.parametrize(
    ("action", "current", "proposed", "change_ratio"),
    [
        (Action.DECREASE_BUDGET, Decimal(100), Decimal(90), Decimal("-0.10")),
        (Action.INCREASE_TARGET_ROAS, Decimal(2), Decimal("2.2"), Decimal("0.10")),
    ],
)
def test_protection_actions_can_succeed_with_flat_gmv_when_efficiency_improves(
    action: Action,
    current: Decimal,
    proposed: Decimal,
    change_ratio: Decimal,
    service: EvaluatorService,
    repository: OptimizerRepository,
):
    recommendation = repository.save_recommendation(
        _recommendation(
            action=action,
            current_value=current,
            proposed_value=proposed,
            change_ratio=change_ratio,
        )
    )
    feedback = service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        recommendation.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )
    _save_complete_windows(repository, after_gmv="100", after_spend="35")

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 12), _at(13)
    )

    assert result.status is EvaluationStatus.EFFECTIVE


def test_pause_with_complete_zero_spend_days_is_effective_stop_loss(
    service: EvaluatorService, repository: OptimizerRepository
):
    recommendation = repository.save_recommendation(
        _recommendation(
            action=Action.PAUSE,
            current_value=None,
            proposed_value=None,
            change_ratio=None,
        )
    )
    feedback = service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        recommendation.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )
    _save_complete_windows(
        repository, after_gmv="0", after_spend="0", after_orders=0
    )

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 12), _at(13)
    )

    assert result.status is EvaluationStatus.EFFECTIVE
    assert result.after_stats.roas is None


@pytest.mark.parametrize("action", [Action.CREATE_GROUP, Action.SPLIT_GROUP])
def test_structure_action_without_attribution_scope_is_not_claimed_effective(
    action: Action,
    service: EvaluatorService,
    repository: OptimizerRepository,
):
    recommendation = repository.save_recommendation(
        _recommendation(
            action=action,
            current_value=None,
            proposed_value=None,
            change_ratio=None,
        )
    )
    feedback = service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        recommendation.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )
    _save_complete_windows(repository, after_gmv="150", after_spend="40")

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 12), _at(13)
    )

    assert result.status is EvaluationStatus.INSUFFICIENT_DATA
    assert "结构" in result.note


@pytest.mark.parametrize(
    ("action", "current", "proposed", "change_ratio"),
    [
        (
            Action.DECREASE_TARGET_ROAS,
            Decimal(2),
            Decimal("1.8"),
            Decimal("-0.10"),
        ),
        (Action.RESUME, None, None, None),
    ],
)
def test_other_growth_actions_use_frozen_gmv_and_roas_conditions(
    action: Action,
    current: Decimal | None,
    proposed: Decimal | None,
    change_ratio: Decimal | None,
    service: EvaluatorService,
    repository: OptimizerRepository,
):
    recommendation = repository.save_recommendation(
        _recommendation(
            action=action,
            current_value=current,
            proposed_value=proposed,
            change_ratio=change_ratio,
        )
    )
    feedback = service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        recommendation.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )
    _save_complete_windows(repository, after_gmv="120", after_spend="40")

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 12), _at(13)
    )

    assert result.status is EvaluationStatus.EFFECTIVE


@pytest.mark.parametrize(
    "action",
    [
        Action.INCREASE_BUDGET,
        Action.DECREASE_TARGET_ROAS,
        Action.RESUME,
        Action.CREATE_GROUP,
        Action.SPLIT_GROUP,
    ],
)
def test_zero_spend_growth_window_is_not_reliably_attributed(
    action: Action,
    service: EvaluatorService,
    repository: OptimizerRepository,
):
    numeric = action in {Action.INCREASE_BUDGET, Action.DECREASE_TARGET_ROAS}
    current = Decimal(100) if action is Action.INCREASE_BUDGET else Decimal(2)
    proposed = Decimal(110) if action is Action.INCREASE_BUDGET else Decimal("1.8")
    recommendation = repository.save_recommendation(
        _recommendation(
            action=action,
            current_value=current if numeric else None,
            proposed_value=proposed if numeric else None,
            change_ratio=(
                Decimal("0.10")
                if action is Action.INCREASE_BUDGET
                else Decimal("-0.10") if numeric else None
            ),
        )
    )
    feedback = service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        recommendation.revision,
        FeedbackStatus.EXECUTED,
        executed_at=_at(9, 10),
    )
    _save_complete_windows(repository, after_gmv="0", after_spend="0", after_orders=0)

    result = service.evaluate_feedback(
        "store-1", feedback.feedback_id, date(2026, 9, 12), _at(13)
    )

    assert result.status is EvaluationStatus.INSUFFICIENT_DATA


def test_evaluation_is_idempotent_and_newly_available_fact_creates_revision(
    service: EvaluatorService, repository: OptimizerRepository
):
    feedback = _executed_feedback(service, repository)
    _save_complete_windows(repository, after_gmv="103")

    first = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(13)
    )
    same = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(13)
    )
    repository.upsert_metric(_metric(12, spend="40", gmv="150", orders=3, collected_day=14))
    historical_replay = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(13)
    )
    changed = service.evaluate_feedback(
        "store-1", feedback.feedback_id, as_of=date(2026, 9, 12), evaluated_at=_at(14)
    )

    assert (first.revision, same.revision, historical_replay.revision, changed.revision) == (
        1,
        1,
        1,
        2,
    )
    assert first.input_hash != changed.input_hash


def test_deferred_or_rejected_feedback_cannot_be_evaluated(
    service: EvaluatorService, repository: OptimizerRepository
):
    recommendation = repository.save_recommendation(_recommendation())
    feedback = service.record_feedback(
        "store-1",
        recommendation.recommendation_id,
        1,
        FeedbackStatus.DEFERRED,
        executed_at=_at(9, 10),
    )

    with pytest.raises(ValueError, match="executed or modified"):
        service.evaluate_feedback(
            "store-1", feedback.feedback_id, date(2026, 9, 12), _at(13)
        )


def test_effectiveness_uses_completed_results_as_of_date_and_campaign_action_fallback(
    service: EvaluatorService, repository: OptimizerRepository
):
    for index, effective in enumerate((True, False), start=1):
        recommendation = repository.save_recommendation(
            _recommendation(
                recommendation_id=f"rec-{index}",
                data_date=date(2026, 8, index),
                input_hash=f"input-{index}",
                current_value=Decimal(100),
                proposed_value=Decimal(110 + index * 5),
                change_ratio=Decimal("0.15") if index == 1 else Decimal("0.20"),
                generated_at=datetime(2026, 8, index + 1, tzinfo=UTC),
            )
        )
        feedback = service.record_feedback(
            "store-1",
            recommendation.recommendation_id,
            recommendation.revision,
            FeedbackStatus.EXECUTED,
            executed_at=datetime(2026, 8, index + 2, tzinfo=UTC),
        )
        repository.save_evaluation(
            "store-1",
            _historical_evaluation(
                recommendation,
                feedback.feedback_id,
                EvaluationStatus.EFFECTIVE if effective else EvaluationStatus.NEGATIVE,
                datetime(2026, 8, 20 + index, tzinfo=UTC),
            ),
        )

    exact = service.effectiveness_summary(
        "store-1", "campaign-1", Action.INCREASE_BUDGET, date(2026, 8, 31)
    )
    fallback = service.effectiveness_summary(
        "store-1", "new-campaign", Action.INCREASE_BUDGET, date(2026, 8, 31)
    )
    before_results = service.effectiveness_summary(
        "store-1", "campaign-1", Action.INCREASE_BUDGET, date(2026, 8, 10)
    )

    assert exact.sample_size == 2
    assert exact.success_rate == Decimal("0.5")
    assert exact.budget_step == Decimal("0.15")
    assert fallback == exact
    assert before_results.sample_size == 0
    assert before_results.success_rate is None
    assert before_results.budget_step is None


@pytest.mark.parametrize(
    ("positive_action", "opposite_action", "current", "positive_value", "opposite_value"),
    [
        (
            Action.INCREASE_BUDGET,
            Action.DECREASE_BUDGET,
            Decimal(100),
            Decimal(110),
            Decimal(90),
        ),
        (
            Action.INCREASE_TARGET_ROAS,
            Action.DECREASE_TARGET_ROAS,
            Decimal(2),
            Decimal("2.2"),
            Decimal("1.8"),
        ),
    ],
)
def test_effectiveness_never_mixes_opposite_exact_actions(
    positive_action: Action,
    opposite_action: Action,
    current: Decimal,
    positive_value: Decimal,
    opposite_value: Decimal,
    service: EvaluatorService,
    repository: OptimizerRepository,
):
    for index, (action, proposed, outcome) in enumerate(
        (
            (positive_action, positive_value, EvaluationStatus.NEGATIVE),
            (positive_action, positive_value, EvaluationStatus.EFFECTIVE),
            (opposite_action, opposite_value, EvaluationStatus.EFFECTIVE),
            (opposite_action, opposite_value, EvaluationStatus.EFFECTIVE),
        ),
        start=1,
    ):
        recommendation = repository.save_recommendation(
            _recommendation(
                recommendation_id=f"exact-action-{positive_action.value}-{index}",
                campaign_id=("campaign-1" if index <= 2 else "campaign-2"),
                data_date=date(2026, 8, index),
                action=action,
                current_value=current,
                proposed_value=proposed,
                change_ratio=(proposed / current - Decimal(1)),
                input_hash=f"exact-action-input-{positive_action.value}-{index}",
                generated_at=datetime(2026, 8, index + 1, tzinfo=UTC),
            )
        )
        feedback = service.record_feedback(
            "store-1",
            recommendation.recommendation_id,
            recommendation.revision,
            FeedbackStatus.EXECUTED,
            executed_at=datetime(2026, 8, index + 2, tzinfo=UTC),
        )
        repository.save_evaluation(
            "store-1",
            _historical_evaluation(
                recommendation,
                feedback.feedback_id,
                outcome,
                datetime(2026, 8, 20 + index, tzinfo=UTC),
            ),
        )

    exact = service.effectiveness_summary(
        "store-1", "campaign-1", positive_action, date(2026, 8, 31)
    )
    fallback = service.effectiveness_summary(
        "store-1", "new-campaign", opposite_action, date(2026, 8, 31)
    )

    assert (exact.sample_size, exact.success_rate) == (2, Decimal("0.5"))
    assert (fallback.sample_size, fallback.success_rate) == (2, Decimal(1))
    expected_exact_step = abs(positive_value / current - Decimal(1))
    expected_fallback_step = abs(opposite_value / current - Decimal(1))
    if positive_action in {Action.INCREASE_BUDGET, Action.DECREASE_BUDGET}:
        assert exact.budget_step == expected_exact_step
        assert fallback.budget_step == expected_fallback_step
    else:
        assert exact.target_roas_step == expected_exact_step
        assert fallback.target_roas_step == expected_fallback_step


def test_insufficient_and_pending_results_are_not_counted_as_failure(
    service: EvaluatorService, repository: OptimizerRepository
):
    recommendation = repository.save_recommendation(_recommendation())
    feedback = _executed_feedback(service, repository)
    for index, status in enumerate(
        (EvaluationStatus.PENDING, EvaluationStatus.INSUFFICIENT_DATA), start=1
    ):
        repository.save_evaluation(
            "store-1",
            replace(
                _historical_evaluation(
                    recommendation, feedback.feedback_id, status, _at(20)
                ),
                evaluation_id=f"evaluation-incomplete-{index}",
                input_hash=f"incomplete-{index}",
                revision=index,
            ),
        )

    summary = service.effectiveness_summary(
        "store-1", "campaign-1", Action.INCREASE_BUDGET, date(2026, 9, 30)
    )
    assert summary.sample_size == 0
    assert summary.success_rate is None
    assert summary.budget_step is None


def test_pending_feedback_uses_only_latest_evaluation_revision(
    service: EvaluatorService, repository: OptimizerRepository
):
    recommendation = repository.save_recommendation(_recommendation())
    feedback = _executed_feedback(service, repository)
    completed = _historical_evaluation(
        recommendation, feedback.feedback_id, EvaluationStatus.EFFECTIVE, _at(20)
    )
    repository.save_evaluation("store-1", completed)
    latest = repository.save_evaluation(
        "store-1",
        replace(
            completed,
            evaluation_id="evaluation-latest-pending",
            status=EvaluationStatus.PENDING,
            input_hash="latest-pending-input",
            revision=1,
        ),
    )

    assert latest.revision == 2
    assert service.pending_feedback("store-1") == (feedback,)


def test_evaluation_revision_allocation_is_atomic_and_hash_idempotent(
    service: EvaluatorService, repository: OptimizerRepository
):
    recommendation = repository.save_recommendation(_recommendation())
    feedback = _executed_feedback(service, repository)
    base = _historical_evaluation(
        recommendation, feedback.feedback_id, EvaluationStatus.NO_CHANGE, _at(20)
    )
    database = repository.database
    first_repository = OptimizerRepository(database)
    second_repository = OptimizerRepository(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved = tuple(
            executor.map(
                lambda item: (
                    first_repository if item == "a" else second_repository
                ).save_evaluation(
                    "store-1",
                    replace(
                        base,
                        evaluation_id=f"concurrent-evaluation-{item}",
                        input_hash=f"concurrent-input-{item}",
                        revision=1,
                    ),
                ),
                ("a", "b"),
            )
        )

    assert {item.revision for item in saved} == {1, 2}
    same_hash = repository.save_evaluation(
        "store-1",
        replace(
            base,
            evaluation_id="different-id-same-hash",
            input_hash=saved[0].input_hash,
            revision=99,
        ),
    )
    assert same_hash == saved[0]


def test_pending_feedback_uses_one_set_query(
    service: EvaluatorService,
    repository: OptimizerRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    for index in range(12):
        recommendation = repository.save_recommendation(
            _recommendation(
                recommendation_id=f"pending-query-{index}",
                data_date=date(2026, 8, index + 1),
                input_hash=f"pending-query-input-{index}",
            )
        )
        service.record_feedback(
            "store-1",
            recommendation.recommendation_id,
            recommendation.revision,
            FeedbackStatus.EXECUTED,
            executed_at=_at(20),
        )
    statements: list[str] = []
    original_connect = repository.database.connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository.database, "connect", traced_connect)

    assert len(service.pending_feedback("store-1")) == 12
    assert sum(statement.lstrip().upper().startswith("SELECT") or statement.lstrip().upper().startswith("WITH") for statement in statements) == 1


def test_effectiveness_uses_bounded_exact_action_queries(
    service: EvaluatorService,
    repository: OptimizerRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    recommendation = repository.save_recommendation(_recommendation())
    feedback = _executed_feedback(service, repository)
    repository.save_evaluation(
        "store-1",
        _historical_evaluation(
            recommendation, feedback.feedback_id, EvaluationStatus.EFFECTIVE, _at(20)
        ),
    )
    statements: list[str] = []
    original_connect = repository.database.connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository.database, "connect", traced_connect)

    service.effectiveness_summary(
        "store-1", "campaign-1", Action.INCREASE_BUDGET, date(2026, 9, 30)
    )

    query_count = sum(
        statement.lstrip().upper().startswith(("SELECT", "WITH"))
        for statement in statements
    )
    assert query_count <= 2

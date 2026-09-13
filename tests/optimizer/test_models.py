from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from adwatch.optimizer.models import (
    Action,
    AdMetric,
    CampaignProfile,
    CampaignSnapshot,
    CampaignStatus,
    Confidence,
    DataQuality,
    DataQualityStatus,
    Evaluation,
    EvaluationStatus,
    Evidence,
    Feedback,
    FeedbackStatus,
    MetricGrain,
    ProfileStatus,
    Recommendation,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)


def _observed_at() -> datetime:
    return datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def _metric(**overrides: object) -> AdMetric:
    values: dict[str, object] = {
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "campaign_name": "秋季上新",
        "item_id": "item-1",
        "data_date": date(2026, 9, 7),
        "currency": "CNY",
        "spend": Decimal(100),
        "gmv": Decimal(350),
        "orders": 4,
        "collected_at": _observed_at(),
        "source": "shopee_ads_export",
    }
    values.update(overrides)
    return AdMetric(**values)


def _window_stats(**overrides: object) -> WindowStats:
    values: dict[str, object] = {
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 7),
        "spend": Decimal(100),
        "gmv": Decimal(350),
        "orders": 4,
    }
    values.update(overrides)
    return WindowStats(**values)


def _profile(**overrides: object) -> CampaignProfile:
    values: dict[str, object] = {
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "status": ProfileStatus.FORMAL,
        "effective_from": date(2026, 9, 1),
        "minimum_roas": Decimal("2.0"),
        "minimum_gmv_growth": Decimal("0.05"),
        "growth_intensity": Decimal("0.5"),
        "max_budget_step": Decimal("0.2"),
        "max_target_roas_step": Decimal("0.1"),
        "min_orders": 3,
        "cooldown_days": 3,
        "evaluation_days": 7,
        "rollback_days": 3,
        "allowed_actions": frozenset({Action.INCREASE_BUDGET}),
    }
    values.update(overrides)
    return CampaignProfile(**values)


def _recommendation(**overrides: object) -> Recommendation:
    values: dict[str, object] = {
        "recommendation_id": "rec-1",
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "data_date": date(2026, 9, 7),
        "action": Action.INCREASE_BUDGET,
        "confidence": Confidence.HIGH,
        "priority": 1,
        "current_value": Decimal(100),
        "proposed_value": Decimal(120),
        "change_ratio": Decimal("0.2"),
        "evidence": (
            Evidence(
                code="roas_above_target",
                summary="过去 7 天 ROAS 高于目标",
                short_window=_window_stats(),
                long_window=_window_stats(
                    start_date=date(2026, 8, 9),
                    end_date=date(2026, 9, 7),
                ),
                quality=DataQuality(
                    status=DataQualityStatus.HEALTHY,
                    assessed_at=_observed_at(),
                ),
                rule_inputs=(
                    ("minimum_roas", "2.0"),
                    ("max_budget_step", "0.2"),
                ),
            ),
        ),
        "success_condition": SuccessCondition(
            minimum_roas=Decimal("2.0"),
            minimum_gmv_growth=Decimal("0.05"),
            observation_days=7,
        ),
        "rollback_condition": RollbackCondition(
            roas_below=Decimal("1.5"),
            consecutive_days=3,
        ),
        "expected_impact": Decimal("0.10"),
        "risk": Decimal("0.20"),
        "observation_days": 7,
        "rule_version": "v1",
        "input_hash": "abc123",
        "revision": 1,
        "generated_at": _observed_at(),
    }
    values.update(overrides)
    return Recommendation(**values)


def test_enum_values_are_stable_contracts():
    assert tuple(action.value for action in Action) == (
        "increase_budget",
        "decrease_budget",
        "decrease_target_roas",
        "increase_target_roas",
        "hold",
        "pause",
        "resume",
        "create_group",
        "split_group",
    )
    assert tuple(value.value for value in Confidence) == ("high", "medium", "low")
    assert tuple(value.value for value in FeedbackStatus) == (
        "executed",
        "modified",
        "deferred",
        "rejected",
    )
    assert tuple(value.value for value in EvaluationStatus) == (
        "effective",
        "no_change",
        "negative",
        "insufficient_data",
        "pending",
    )
    assert tuple(value.value for value in DataQualityStatus) == (
        "healthy",
        "degraded",
        "stale",
        "invalid",
    )
    assert tuple(value.value for value in CampaignStatus) == (
        "active",
        "paused",
        "ended",
        "unknown",
    )
    assert tuple(value.value for value in MetricGrain) == (
        "overview",
        "campaign",
        "item",
    )


def test_ad_metric_daily_fact_calculates_roas_and_cpa():
    metric = _metric()

    assert metric.roas == Decimal("3.5000")
    assert metric.cpa == Decimal("25.0000")
    assert metric.data_date == date(2026, 9, 7)
    assert metric.item_id == "item-1"


def test_ad_metric_rejects_empty_stable_identifier():
    with pytest.raises(ValueError, match="item_id"):
        _metric(item_id="  ")


@pytest.mark.parametrize("raw, expected", [("฿", "THB"), ("USD", "USD"), ("¥", "CNY")])
def test_ad_metric_and_campaign_snapshot_normalize_currency_symbols(raw, expected):
    assert _metric(currency=raw).currency == expected
    snapshot = CampaignSnapshot(
        "store-1",
        "campaign-1",
        "活动",
        "auto",
        CampaignStatus.ACTIVE,
        Decimal(1),
        Decimal(2),
        raw,
        _observed_at(),
        date(2026, 9, 7),
    )
    assert snapshot.currency == expected


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_decimal_fields_reject_non_finite_values(value: Decimal):
    with pytest.raises(ValueError, match="spend must be finite"):
        _metric(spend=value)

    with pytest.raises(ValueError, match="growth_intensity must be finite"):
        _profile(growth_intensity=value)


@pytest.mark.parametrize("field", ["spend", "gmv", "orders"])
def test_ad_metric_rejects_negative_indicators(field: str):
    value = Decimal(-1) if field in {"spend", "gmv"} else -1

    with pytest.raises(ValueError, match=field):
        _metric(**{field: value})


def test_daily_fact_requires_timezone_aware_collection_time():
    with pytest.raises(ValueError, match="collected_at"):
        _metric(collected_at=_observed_at().replace(tzinfo=None))


def test_data_quality_allows_aggressive_scale_only_for_complete_healthy_data():
    quality = DataQuality(
        status=DataQualityStatus.HEALTHY,
        assessed_at=_observed_at(),
        allow_aggressive_scale=True,
    )

    assert quality.allow_aggressive_scale is True

    with pytest.raises(ValueError, match="allow_aggressive_scale"):
        DataQuality(
            status=DataQualityStatus.DEGRADED,
            assessed_at=_observed_at(),
            allow_aggressive_scale=True,
        )


def test_campaign_snapshot_is_control_plane_without_metric_binding():
    snapshot = CampaignSnapshot(
        store_id="store-1",
        campaign_id="campaign-1",
        campaign_name="秋季上新",
        campaign_type="auto",
        status=CampaignStatus.ACTIVE,
        current_budget=Decimal(100),
        target_roas=Decimal("2.0"),
        currency="CNY",
        observed_at=_observed_at(),
        data_date=date(2026, 9, 7),
    )

    assert snapshot.current_budget == Decimal(100)
    assert not hasattr(snapshot, "metric")
    assert not hasattr(snapshot, "data_quality")


def test_campaign_snapshot_preserves_missing_budget_and_target_roas():
    snapshot = CampaignSnapshot(
        store_id="store-1",
        campaign_id="campaign-1",
        campaign_name="预算未加载的活动",
        campaign_type="auto",
        status=CampaignStatus.ACTIVE,
        current_budget=None,
        target_roas=None,
        currency="CNY",
        observed_at=_observed_at(),
        data_date=date(2026, 9, 7),
    )

    assert snapshot.current_budget is None
    assert snapshot.target_roas is None


def test_campaign_profile_rejects_ratio_and_step_outside_mathematical_range():
    with pytest.raises(ValueError, match="minimum_gmv_growth"):
        _profile(minimum_gmv_growth=Decimal("1.01"))

    with pytest.raises(ValueError, match="max_budget_step"):
        _profile(max_budget_step=Decimal("1.01"))

    with pytest.raises(ValueError, match="min_orders"):
        _profile(min_orders=0)


def test_needs_floor_profile_supports_conservative_recommendation_without_roas():
    profile = _profile(
        status=ProfileStatus.NEEDS_FLOOR,
        minimum_roas=None,
        allowed_actions=frozenset({Action.HOLD}),
    )
    recommendation = _recommendation(
        action=Action.HOLD,
        current_value=None,
        proposed_value=None,
        change_ratio=None,
        success_condition=SuccessCondition(
            minimum_roas=None,
            minimum_gmv_growth=Decimal("0.05"),
            observation_days=7,
        ),
        rollback_condition=RollbackCondition(
            roas_below=None,
            consecutive_days=3,
        ),
    )

    assert profile.minimum_roas is None
    assert recommendation.success_condition.minimum_roas is None
    assert recommendation.rollback_condition.roas_below is None


def test_recommendation_has_structured_evidence_and_conditions():
    recommendation = _recommendation()

    evidence = recommendation.evidence[0]
    assert evidence.short_window.roas == Decimal("3.5000")
    assert evidence.long_window.start_date == date(2026, 8, 9)
    assert evidence.quality.allow_aggressive_scale is False
    assert evidence.rule_inputs == (
        ("minimum_roas", "2.0"),
        ("max_budget_step", "0.2"),
    )
    assert recommendation.success_condition.minimum_gmv_growth == Decimal("0.05")
    assert recommendation.rollback_condition.roas_below == Decimal("1.5")


def test_recommendation_rejects_invalid_change_ratio():
    with pytest.raises(ValueError, match="change_ratio"):
        _recommendation(change_ratio=Decimal("1.01"))


@pytest.mark.parametrize(
    ("action", "current_value", "proposed_value"),
    [
        (
            Action.INCREASE_BUDGET,
            Decimal("99.99"),
            Decimal("109.99"),
        ),
        (
            Action.INCREASE_TARGET_ROAS,
            Decimal("2.35"),
            Decimal("2.59"),
        ),
    ],
)
def test_recommendation_accepts_change_ratio_at_proposed_value_precision(
    action, current_value, proposed_value
):
    recommendation = _recommendation(
        action=action,
        current_value=current_value,
        proposed_value=proposed_value,
        change_ratio=Decimal("0.10"),
    )

    assert recommendation.proposed_value == proposed_value


def test_recommendation_rejects_change_ratio_outside_proposed_value_precision():
    with pytest.raises(ValueError, match="change_ratio"):
        _recommendation(
            current_value=Decimal("99.99"),
            proposed_value=Decimal("110.00"),
            change_ratio=Decimal("0.10"),
        )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        (
            {
                "action": Action.INCREASE_BUDGET,
                "proposed_value": Decimal(90),
                "change_ratio": Decimal("0.1"),
            },
            "proposed_value",
        ),
        (
            {
                "action": Action.DECREASE_BUDGET,
                "proposed_value": Decimal(90),
                "change_ratio": Decimal("-0.2"),
            },
            "change_ratio",
        ),
        (
            {
                "action": Action.INCREASE_BUDGET,
                "current_value": Decimal(0),
                "proposed_value": Decimal(20),
                "change_ratio": Decimal("0.2"),
            },
            "current_value",
        ),
        (
            {
                "action": Action.HOLD,
                "current_value": Decimal(100),
                "proposed_value": Decimal(100),
                "change_ratio": Decimal(0),
            },
            "hold",
        ),
    ],
)
def test_recommendation_rejects_incoherent_action_values(overrides, field):
    with pytest.raises(ValueError, match=field):
        _recommendation(**overrides)


def test_feedback_and_evaluation_use_execution_and_observation_contracts():
    feedback = Feedback(
        feedback_id="feedback-1",
        recommendation_id="rec-1",
        status=FeedbackStatus.MODIFIED,
        actual_value=Decimal(115),
        executed_at=_observed_at(),
        note="运营人员降低了调幅",
    )
    evaluation = Evaluation(
        evaluation_id="evaluation-1",
        recommendation_id="rec-1",
        status=EvaluationStatus.EFFECTIVE,
        baseline_start=date(2026, 9, 1),
        baseline_end=date(2026, 9, 7),
        observation_start=date(2026, 9, 8),
        observation_end=date(2026, 9, 14),
        algorithm_version="v1",
        evaluated_at=_observed_at(),
        baseline_stats=_window_stats(),
        after_stats=_window_stats(
            start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 14),
            spend=Decimal(100),
            gmv=Decimal(480),
        ),
    )

    assert feedback.actual_value == Decimal(115)
    assert evaluation.after_stats.roas == Decimal("4.8000")
    assert evaluation.algorithm_version == "v1"
    assert not hasattr(evaluation, "baseline_metric")
    assert not hasattr(evaluation, "outcome_metric")


@pytest.mark.parametrize(
    ("status", "actual_value"),
    [
        (FeedbackStatus.MODIFIED, None),
        (FeedbackStatus.EXECUTED, Decimal(115)),
        (FeedbackStatus.DEFERRED, Decimal(115)),
        (FeedbackStatus.REJECTED, Decimal(115)),
    ],
)
def test_feedback_status_controls_actual_value(
    status: FeedbackStatus, actual_value: Decimal | None
):
    with pytest.raises(ValueError, match="actual_value"):
        Feedback(
            "feedback-1",
            "rec-1",
            status,
            actual_value,
            _observed_at(),
        )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        (
            {"observation_start": date(2026, 9, 7)},
            "observation_start",
        ),
        (
            {
                "baseline_start": date(2026, 8, 31),
            },
            "baseline_stats",
        ),
    ],
)
def test_evaluation_requires_separate_and_matching_windows(overrides, field):
    values: dict[str, object] = {
        "evaluation_id": "evaluation-1",
        "recommendation_id": "rec-1",
        "status": EvaluationStatus.EFFECTIVE,
        "baseline_start": date(2026, 9, 1),
        "baseline_end": date(2026, 9, 7),
        "observation_start": date(2026, 9, 8),
        "observation_end": date(2026, 9, 14),
        "algorithm_version": "v1",
        "evaluated_at": _observed_at(),
        "baseline_stats": _window_stats(),
        "after_stats": _window_stats(
            start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 14),
        ),
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=field):
        Evaluation(**values)


def test_complete_construction_contracts_are_immutable():
    metric = _metric()
    profile = _profile()
    recommendation = _recommendation()

    assert profile.rollback_days == 3
    assert recommendation.expected_impact == Decimal("0.10")
    with pytest.raises(AttributeError):
        metric.spend = Decimal(200)
    with pytest.raises(AttributeError):
        recommendation.evidence[0].quality.status = DataQualityStatus.STALE
    with pytest.raises(AttributeError):
        recommendation.evidence[0].rule_inputs = ()

    with pytest.raises(TypeError, match="allowed_actions"):
        _profile(allowed_actions=[Action.INCREASE_BUDGET])

    with pytest.raises(TypeError, match="evidence"):
        _recommendation(evidence=[])

    with pytest.raises(TypeError, match="rule_inputs"):
        Evidence(
            code="roas_above_target",
            summary="过去 7 天 ROAS 高于目标",
            short_window=_window_stats(),
            long_window=_window_stats(),
            quality=DataQuality(
                status=DataQualityStatus.HEALTHY,
                assessed_at=_observed_at(),
            ),
            rule_inputs=[("minimum_roas", "2.0")],
        )

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from adwatch.config import Settings
from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.models import (
    Action,
    AdMetric,
    CampaignProfile,
    CampaignSnapshot,
    CampaignStatus,
    DataQuality,
    DataQualityStatus,
    MetricGrain,
    ProfileStatus,
)
from adwatch.optimizer.policy import EffectivenessSummary, Optimizer
from adwatch.optimizer.repository import OptimizerRepository


def _now() -> datetime:
    return datetime(2026, 9, 10, 9, tzinfo=UTC)


def _decision_at() -> datetime:
    return datetime(2026, 9, 10, 10, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path) -> OptimizerRepository:
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    return OptimizerRepository(database)


def _settings(tmp_path, **changes: object) -> Settings:
    values: dict[str, object] = {"data_dir": tmp_path}
    values.update(changes)
    return Settings(**values)


def _quality(**changes: object) -> DataQuality:
    values: dict[str, object] = {
        "status": DataQualityStatus.HEALTHY,
        "assessed_at": _now(),
    }
    values.update(changes)
    return DataQuality(**values)


def _snapshot(**changes: object) -> CampaignSnapshot:
    values: dict[str, object] = {
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "campaign_name": "秋季活动",
        "campaign_type": "auto",
        "status": CampaignStatus.ACTIVE,
        "current_budget": Decimal("100.00"),
        "target_roas": Decimal("2.00"),
        "currency": "CNY",
        "observed_at": _now(),
        "data_date": date(2026, 9, 10),
    }
    values.update(changes)
    return CampaignSnapshot(**values)


def _metric(day: date, **changes: object) -> AdMetric:
    values: dict[str, object] = {
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "campaign_name": "秋季活动",
        "item_id": "__ALL__",
        "data_date": day,
        "currency": "CNY",
        "spend": Decimal("100.00"),
        "gmv": Decimal("300.00"),
        "orders": 4,
        "collected_at": _now(),
        "source": "fixture",
        "grain": MetricGrain.CAMPAIGN,
    }
    values.update(changes)
    return AdMetric(**values)


def _formal_profile(**changes: object) -> CampaignProfile:
    values: dict[str, object] = {
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "status": ProfileStatus.FORMAL,
        "effective_from": date(2026, 9, 1),
        "minimum_roas": Decimal("2.00"),
        "minimum_gmv_growth": Decimal("0.05"),
        "growth_intensity": Decimal("0.50"),
        "max_budget_step": Decimal("0.20"),
        "max_target_roas_step": Decimal("0.20"),
        "min_orders": 3,
        "cooldown_days": 2,
        "evaluation_days": 7,
        "rollback_days": 3,
        "allowed_actions": frozenset(Action),
    }
    values.update(changes)
    return CampaignProfile(**values)


def _save_campaign_days(
    repository: OptimizerRepository,
    *,
    gmv: Decimal,
    spend: Decimal = Decimal("100.00"),
    orders: int = 4,
):
    for day in (date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)):
        repository.upsert_metric(_metric(day, spend=spend, gmv=gmv, orders=orders))


def test_healthy_saturated_campaign_increases_budget_with_frozen_evidence(
    repository: OptimizerRepository, tmp_path
):
    target_date = date(2026, 9, 10)
    repository.save_snapshot(_snapshot())
    for day in (date(2026, 9, 8), date(2026, 9, 9), target_date):
        repository.upsert_metric(_metric(day))

    recommendations = Optimizer(
        repository,
        _settings(tmp_path, min_history_days=3, short_window_days=3, long_window_days=3),
    ).recommend("store-1", "campaign-1", target_date, _quality(), _decision_at())

    assert len(recommendations) == 1
    recommendation = recommendations[0]
    assert recommendation.action is Action.INCREASE_BUDGET
    assert recommendation.current_value == Decimal("100.00")
    assert recommendation.proposed_value == Decimal("105.00")
    assert recommendation.change_ratio == Decimal("0.05")
    assert recommendation.evidence[0].short_window.spend == Decimal("300.00")
    assert recommendation.evidence[0].short_window.gmv == Decimal("900.00")
    assert recommendation.evidence[0].short_window.roas == Decimal("3.0000")
    assert recommendation.evidence[0].long_window.cpa == Decimal("25.0000")
    assert recommendation.evidence[0].quality == _quality()
    assert dict(recommendation.evidence[0].rule_inputs)["policy_template"]
    assert recommendation.success_condition.minimum_roas == Decimal("2.00")
    assert recommendation.rollback_condition.roas_below is not None
    assert recommendation.rule_version
    assert recommendation.input_hash
    assert recommendation.revision == 1


def test_ensure_profile_initializes_from_snapshot_without_writing_recommendation(
    repository: OptimizerRepository, tmp_path
) -> None:
    repository.save_snapshot(_snapshot())
    optimizer = Optimizer(repository, _settings(tmp_path))

    profile = optimizer.ensure_profile(
        "store-1", "campaign-1", date(2026, 9, 10), _decision_at()
    )

    assert profile.minimum_roas == Decimal("2.00")
    assert repository.list_recommendations(
        "store-1", "campaign-1", date(2026, 9, 10)
    ) == ()


def test_invalid_or_incomplete_quality_never_emits_numeric_action(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal("300.00"))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1",
        "campaign-1",
        date(2026, 9, 10),
        _quality(status=DataQualityStatus.STALE),
        _decision_at(),
    )[0]

    assert recommendation.action is Action.HOLD
    assert recommendation.confidence.value == "low"
    assert (
        recommendation.current_value,
        recommendation.proposed_value,
        recommendation.change_ratio,
    ) == (None, None, None)


def test_missing_floor_immediately_holds_without_inventing_target(repository, tmp_path):
    repository.save_snapshot(_snapshot(target_roas=None))
    _save_campaign_days(repository, gmv=Decimal("300.00"))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    profile = repository.get_current_profile("store-1", "campaign-1")
    assert profile is not None and profile.status is ProfileStatus.NEEDS_FLOOR
    assert profile.minimum_roas is None
    assert recommendation.action is Action.HOLD
    assert recommendation.success_condition.minimum_roas is None


def test_formal_profile_is_not_overwritten_by_later_snapshot(repository, tmp_path):
    repository.save_snapshot(_snapshot(target_roas=Decimal("1.00")))
    repository.save_profile(_formal_profile(minimum_roas=Decimal("3.00")))
    _save_campaign_days(repository, gmv=Decimal("400.00"))

    Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )

    assert repository.get_current_profile(
        "store-1", "campaign-1"
    ).minimum_roas == Decimal("3.00")


def test_temporary_profile_tracks_a_newer_snapshot_and_versions_are_immutable(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot(target_roas=Decimal("2.00")))
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    optimizer = Optimizer(repository, _settings(tmp_path))
    optimizer.recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())
    repository.save_snapshot(
        _snapshot(
            target_roas=Decimal("3.00"), observed_at=_now() + timedelta(minutes=1)
        )
    )

    optimizer.recommend("store-1", "campaign-1", date(2026, 9, 11), _quality(), _decision_at())

    versions = repository.list_profile_versions("store-1", "campaign-1")
    assert [item.minimum_roas for item in versions] == [
        Decimal("2.00"),
        Decimal("3.00"),
    ]


def test_paused_campaign_only_recommends_resume_after_healthy_recovery(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot(status=CampaignStatus.PAUSED))
    _save_campaign_days(repository, gmv=Decimal("300.00"))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.action is Action.RESUME
    assert recommendation.current_value is None


def test_zero_spend_or_orders_remains_explainable_hold(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(
        repository, spend=Decimal("0.00"), gmv=Decimal("0.00"), orders=0
    )

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.action is Action.HOLD
    assert recommendation.evidence[0].long_window.roas is None
    assert recommendation.evidence[0].long_window.cpa is None


def test_cooldown_holds_and_reports_no_numeric_change(repository, tmp_path):
    optimizer = Optimizer(
        repository,
        _settings(tmp_path, min_history_days=3, short_window_days=3, long_window_days=3),
    )
    repository.save_snapshot(_snapshot())
    repository.save_profile(_formal_profile(cooldown_days=2))
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    first = optimizer.recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[
        0
    ]

    second = optimizer.recommend(
        "store-1", "campaign-1", date(2026, 9, 11), _quality(), _decision_at()
    )[0]

    assert first.action is Action.INCREASE_BUDGET
    assert second.action is Action.HOLD
    assert second.current_value is None


def test_healthy_unsaturated_campaign_lowers_target_when_allowed(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, spend=Decimal("10.00"), gmv=Decimal("30.00"))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.action is Action.DECREASE_TARGET_ROAS
    assert recommendation.current_value == Decimal("2.00")
    assert recommendation.proposed_value == Decimal("1.90")
    assert recommendation.change_ratio == Decimal("-0.05")


def test_disallowed_parameter_action_falls_back_to_hold(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    repository.save_profile(_formal_profile(allowed_actions=frozenset({Action.HOLD})))
    _save_campaign_days(repository, gmv=Decimal("300.00"))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.action is Action.HOLD
    assert recommendation.current_value is None


def test_severe_underperformance_pauses_when_history_is_sufficient(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal("50.00"))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.action is Action.PAUSE
    assert recommendation.current_value is None


def test_short_history_returns_conservative_low_confidence_budget_change(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot())
    repository.upsert_metric(_metric(date(2026, 9, 10), gmv=Decimal("50.00"), orders=4))

    recommendation = Optimizer(
        repository, _settings(tmp_path, min_history_days=3)
    ).recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[0]

    assert recommendation.action is Action.DECREASE_BUDGET
    assert recommendation.confidence.value == "low"
    assert abs(recommendation.change_ratio) <= Decimal("0.05")


def test_campaign_metrics_not_item_metrics_determine_roas_action(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal("50.00"))
    for day in (date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)):
        repository.upsert_metric(
            _metric(
                day, item_id="winner", grain=MetricGrain.ITEM, gmv=Decimal("1000.00")
            )
        )

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.action is Action.PAUSE
    assert recommendation.evidence[0].long_window.gmv == Decimal("150.00")


def test_structural_todos_can_accompany_parameter_recommendation(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    for day in (date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)):
        repository.upsert_metric(
            _metric(
                day,
                item_id="winner",
                grain=MetricGrain.ITEM,
                spend=Decimal("10.00"),
                gmv=Decimal("90.00"),
            )
        )
        repository.upsert_metric(
            _metric(
                day,
                item_id="loser",
                grain=MetricGrain.ITEM,
                spend=Decimal("10.00"),
                gmv=Decimal("10.00"),
            )
        )

    recommendations = Optimizer(
        repository, _settings(tmp_path, short_window_days=3, long_window_days=3)
    ).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )

    assert {item.action for item in recommendations} == {
        Action.INCREASE_BUDGET,
        Action.CREATE_GROUP,
        Action.SPLIT_GROUP,
    }
    for item in recommendations[1:]:
        assert item.current_value is None
        assert "人工核查" in item.evidence[0].summary
        assert dict(item.evidence[0].rule_inputs)["item_windows"]


def test_same_inputs_are_idempotent_and_changed_inputs_increment_revision(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    optimizer = Optimizer(repository, _settings(tmp_path))

    first = optimizer.recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[
        0
    ]
    same = optimizer.recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[
        0
    ]
    repository.upsert_metric(_metric(date(2026, 9, 10), gmv=Decimal("400.00")))
    changed = optimizer.recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert (first.revision, same.revision, changed.revision) == (1, 1, 2)


def test_effectiveness_calibration_uses_only_sufficient_sample_and_evaluation_date(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    calls: list[date] = []

    def provider(
        store_id: str, campaign_id: str, data_date: date, action: Action
    ):
        assert (store_id, campaign_id) == ("store-1", "campaign-1")
        assert action is Action.INCREASE_BUDGET
        calls.append(data_date)
        return EffectivenessSummary(sample_size=5, budget_step=Decimal("0.15"))

    recommendation = Optimizer(
        repository,
        _settings(
            tmp_path,
            effectiveness_min_samples=5,
            short_window_days=3,
            long_window_days=3,
        ),
        effectiveness_provider=provider,
    ).recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[0]

    assert recommendation.proposed_value == Decimal("115.00")
    assert calls == [date(2026, 9, 10)]
    inputs = dict(recommendation.evidence[0].rule_inputs)
    assert inputs["sample_count"] == "5"
    assert inputs["recommended_step"] == "0.15"
    assert inputs["effective_step"] == "0.15"


def test_effectiveness_provider_receives_exact_recommended_action(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    calls: list[Action] = []

    def provider(
        store_id: str, campaign_id: str, data_date: date, action: Action
    ) -> EffectivenessSummary:
        calls.append(action)
        return EffectivenessSummary(sample_size=5, budget_step=Decimal("0.12"))

    recommendation = Optimizer(
        repository,
        _settings(
            tmp_path,
            effectiveness_min_samples=5,
            short_window_days=3,
            long_window_days=3,
        ),
        effectiveness_provider=provider,
    ).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.action is Action.INCREASE_BUDGET
    assert calls == [Action.INCREASE_BUDGET]


def test_low_history_keeps_effectiveness_calibration_under_low_confidence_cap(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot())
    repository.upsert_metric(_metric(date(2026, 9, 10), gmv=Decimal("300.00")))

    recommendation = Optimizer(
        repository,
        _settings(tmp_path, effectiveness_min_samples=1),
        effectiveness_provider=lambda *_: EffectivenessSummary(
            sample_size=1, budget_step=Decimal("0.15")
        ),
    ).recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[0]

    assert recommendation.change_ratio <= Decimal("0.05")


def test_disallowed_structure_actions_are_not_emitted(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    repository.save_profile(
        _formal_profile(
            allowed_actions=frozenset({Action.INCREASE_BUDGET, Action.HOLD})
        )
    )
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    for day in (date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)):
        repository.upsert_metric(
            _metric(day, item_id="winner", grain=MetricGrain.ITEM, gmv=Decimal(90))
        )
        repository.upsert_metric(
            _metric(day, item_id="loser", grain=MetricGrain.ITEM, gmv=Decimal(10))
        )

    actions = {
        item.action
    for item in Optimizer(
        repository, _settings(tmp_path, short_window_days=3, long_window_days=3)
    ).recommend(
            "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
        )
    }

    assert actions == {Action.INCREASE_BUDGET}


def test_future_snapshot_does_not_change_historical_replay(repository, tmp_path):
    repository.save_snapshot(
        _snapshot(data_date=date(2026, 9, 10), target_roas=Decimal(2))
    )
    repository.save_snapshot(
        _snapshot(
            data_date=date(2026, 9, 11),
            target_roas=Decimal(9),
            observed_at=_now() + timedelta(minutes=1),
        )
    )
    _save_campaign_days(repository, gmv=Decimal("300.00"))

    historical = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert historical.success_condition.minimum_roas == Decimal(2)
    assert (
        '"data_date":"2026-09-10"'
        in dict(historical.evidence[0].rule_inputs)["snapshot"]
    )


@pytest.mark.parametrize("status", [CampaignStatus.ENDED, CampaignStatus.UNKNOWN])
def test_ended_or_unknown_campaign_only_holds_and_never_emits_structure(
    repository, tmp_path, status
):
    repository.save_snapshot(_snapshot(status=status))
    _save_campaign_days(repository, gmv=Decimal(300))

    recommendations = Optimizer(
        repository, _settings(tmp_path, short_window_days=3, long_window_days=3)
    ).recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())

    assert [item.action for item in recommendations] == [Action.HOLD]


def test_same_business_day_snapshot_after_decision_is_not_used(repository, tmp_path):
    repository.save_snapshot(_snapshot(target_roas=Decimal(2)))
    repository.save_snapshot(
        _snapshot(target_roas=Decimal(8), observed_at=_decision_at() + timedelta(minutes=1))
    )
    _save_campaign_days(repository, gmv=Decimal(300))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.generated_at == _decision_at()
    assert recommendation.success_condition.minimum_roas == Decimal(2)


def test_future_profile_does_not_change_historical_decision(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    repository.save_profile(_formal_profile(minimum_roas=Decimal(2)))
    repository.save_profile(
        _formal_profile(
            minimum_roas=Decimal(9), effective_from=date(2026, 9, 11)
        )
    )
    _save_campaign_days(repository, gmv=Decimal(300))

    recommendation = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert recommendation.success_condition.minimum_roas == Decimal(2)
    assert repository.get_profile_as_of("store-1", "campaign-1", date(2026, 9, 10)).minimum_roas == Decimal(2)


def test_default_step_and_calendar_utilization_control_action(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    for day in (date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)):
        repository.upsert_metric(_metric(day, gmv=Decimal(300)))

    unsaturated = Optimizer(
        repository,
        _settings(tmp_path, default_budget_step=Decimal("0.12"), min_history_days=3),
    ).recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[0]
    saturated = Optimizer(
        repository,
        _settings(
            tmp_path,
            default_budget_step=Decimal("0.12"),
            min_history_days=3,
            short_window_days=3,
            long_window_days=3,
        ),
    ).recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[0]

    assert unsaturated.action is Action.DECREASE_TARGET_ROAS
    assert saturated.action is Action.INCREASE_BUDGET
    assert saturated.proposed_value == Decimal("106.00")


def test_equivalent_decimal_and_non_decisional_times_are_idempotent(repository, tmp_path):
    repository.save_snapshot(_snapshot(observed_at=_now()))
    _save_campaign_days(repository, gmv=Decimal("300.00"))
    optimizer = Optimizer(repository, _settings(tmp_path))
    first = optimizer.recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]
    repository.upsert_metric(
        _metric(date(2026, 9, 10), gmv=Decimal(300), collected_at=_decision_at())
    )
    repository.save_snapshot(_snapshot(observed_at=_now() + timedelta(minutes=30)))
    second = optimizer.recommend(
        "store-1",
        "campaign-1",
        date(2026, 9, 10),
        _quality(assessed_at=_decision_at()),
        _decision_at(),
    )[0]

    assert first.input_hash == second.input_hash
    assert second.revision == 1


def test_missing_snapshot_only_emits_hold_even_when_history_is_severely_unhealthy(
    repository, tmp_path
):
    repository.save_profile(_formal_profile())
    _save_campaign_days(repository, gmv=Decimal(50))

    recommendations = Optimizer(repository, _settings(tmp_path)).recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )

    assert [item.action for item in recommendations] == [Action.HOLD]


def test_effectiveness_summary_rejects_negative_steps():
    with pytest.raises(ValueError, match="budget_step"):
        EffectivenessSummary(sample_size=1, budget_step=Decimal("-0.01"))
    with pytest.raises(ValueError, match="target_roas_step"):
        EffectivenessSummary(sample_size=1, target_roas_step=Decimal("-0.01"))


def test_invalid_effectiveness_provider_value_falls_back_to_default_step(
    repository, tmp_path
):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal(300))

    class UnsafeSummary:
        sample_size = 10
        budget_step = Decimal("-0.15")
        target_roas_step = Decimal("-0.15")
        success_rate = None

    recommendation = Optimizer(
        repository,
        _settings(tmp_path, effectiveness_min_samples=1, short_window_days=3, long_window_days=3),
        effectiveness_provider=lambda *_: UnsafeSummary(),
    ).recommend("store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at())[0]

    assert recommendation.action is Action.INCREASE_BUDGET
    assert recommendation.proposed_value == Decimal("105.00")


def test_equivalent_calibrated_decimal_steps_keep_same_revision(repository, tmp_path):
    repository.save_snapshot(_snapshot())
    _save_campaign_days(repository, gmv=Decimal(300))
    provided = iter((Decimal("0.10"), Decimal("0.1")))
    optimizer = Optimizer(
        repository,
        _settings(tmp_path, effectiveness_min_samples=1, short_window_days=3, long_window_days=3),
        effectiveness_provider=lambda *_: EffectivenessSummary(
            sample_size=1, budget_step=next(provided)
        ),
    )

    first = optimizer.recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]
    second = optimizer.recommend(
        "store-1", "campaign-1", date(2026, 9, 10), _quality(), _decision_at()
    )[0]

    assert first.input_hash == second.input_hash
    assert second.revision == 1

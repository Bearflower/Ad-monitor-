from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import adwatch.optimizer.repository as repository_module
from adwatch.optimizer.database import OptimizerDatabase
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
    RecommendationSlot,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)
from adwatch.optimizer.repository import ClaimLost, OptimizerRepository, RunLog


def _now() -> datetime:
    return datetime(2026, 9, 9, 9, tzinfo=UTC)


def _metric(**changes: object) -> AdMetric:
    values: dict[str, object] = {
        "store_id": "store-1",
        "campaign_id": "campaign-1",
        "campaign_name": "秋季",
        "item_id": "item-1",
        "data_date": date(2026, 9, 7),
        "currency": "CNY",
        "spend": Decimal("1.20"),
        "gmv": Decimal("3.40"),
        "orders": 2,
        "collected_at": _now(),
        "source": "export",
    }
    values.update(changes)
    return AdMetric(**values)


def _window(
    start: date = date(2026, 9, 1), end: date = date(2026, 9, 7)
) -> WindowStats:
    return WindowStats(start, end, Decimal("1.20"), Decimal("3.40"), 2)


def _recommendation(**changes: object) -> Recommendation:
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
                "roas",
                "表现良好",
                _window(),
                _window(date(2026, 8, 9)),
                DataQuality(DataQualityStatus.HEALTHY, _now()),
                (("key", "value"),),
            ),
        ),
        "success_condition": SuccessCondition(Decimal(2), Decimal("0.05"), 7),
        "rollback_condition": RollbackCondition(Decimal("1.5"), 3),
        "expected_impact": Decimal("0.1"),
        "risk": Decimal("0.2"),
        "observation_days": 7,
        "rule_version": "v1",
        "input_hash": "input-1",
        "revision": 1,
        "generated_at": _now(),
    }
    values.update(changes)
    return Recommendation(**values)


@pytest.fixture
def repository(tmp_path: Path) -> OptimizerRepository:
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    return OptimizerRepository(database)


def test_metric_upsert_round_trips_decimal_without_rows(
    repository: OptimizerRepository,
):
    saved = repository.upsert_metric(_metric())
    replaced = repository.upsert_metric(_metric(spend=Decimal("9.99")))

    assert saved.spend == Decimal("1.20")
    assert replaced.spend == Decimal("9.99")
    metrics = repository.list_metrics("store-1", "campaign-1", date(2026, 9, 7))
    assert metrics == (replaced,)
    assert type(metrics[0]).__name__ == "AdMetric"


def test_recommendation_batch_rolls_back_if_a_later_insert_fails(
    repository: OptimizerRepository,
) -> None:
    first = _recommendation(campaign_id="campaign-a")
    colliding = _recommendation(campaign_id="campaign-b")

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_recommendations((first, colliding))

    assert repository.list_recommendations(
        "store-1", "campaign-a", date(2026, 9, 7)
    ) == ()


def test_metric_observation_history_replays_value_visible_at_evaluation_time(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    old = _metric(
        data_date=target,
        spend=Decimal(10),
        collected_at=datetime(2026, 9, 8, 9, tzinfo=UTC),
        grain=MetricGrain.CAMPAIGN,
        item_id="__ALL__",
    )
    corrected = replace(
        old,
        spend=Decimal(20),
        collected_at=datetime(2026, 9, 9, 9, tzinfo=UTC),
    )
    repository.upsert_metric(old)
    repository.upsert_metric(corrected)

    replayed = repository.list_campaign_metrics_between(
        "store-1",
        "campaign-1",
        target,
        target,
        collected_by=datetime(2026, 9, 8, 10, tzinfo=UTC),
    )

    assert replayed == (old,)
    assert repository.list_metrics("store-1", "campaign-1", target) == (corrected,)


def test_latest_metric_date_query_uses_covering_indexes_without_temp_materialization(
    repository: OptimizerRepository,
):
    cutoff = _now()
    with repository.database.connect() as connection:
        plan = tuple(
            row["detail"]
            for row in connection.execute(
                "EXPLAIN QUERY PLAN " + repository_module._LATEST_METRIC_DATE_AS_OF_SQL,
                (
                    "store-1",
                    date(2026, 9, 9).isoformat(),
                    repository_module._datetime_epoch_us(cutoff),
                    repository_module._datetime_epoch_us(cutoff),
                ),
            )
        )

    details = "\n".join(plan)
    assert "optimizer_observation_report_date" in details
    assert "optimizer_observation_logical_latest" in details
    assert "USE TEMP B-TREE" not in details
    assert "MATERIALIZE" not in details


def test_latest_metric_date_respects_campaign_grain_cutoff_and_latest_tombstone(
    repository: OptimizerRepository,
):
    old_date = date(2026, 9, 7)
    newer_date = date(2026, 9, 8)
    first_seen = datetime.fromisoformat("2026-09-09T09:00:00.100000+08:00")
    removed_at = datetime.fromisoformat("2026-09-09T01:00:00.900000+00:00")
    repository.upsert_metric(
        _metric(data_date=old_date, collected_at=first_seen, item_id="__ALL__")
    )
    campaign = _metric(data_date=newer_date, collected_at=first_seen, item_id="__ALL__")
    repository.upsert_metric(campaign)
    repository.replace_store_day(
        "store-1",
        newer_date,
        (
            replace(
                campaign,
                grain=MetricGrain.ITEM,
                item_id="item-only",
                collected_at=removed_at,
            ),
        ),
        (),
        "success",
    )

    assert (
        repository.latest_metric_date_as_of(
            "store-1",
            newer_date,
            collected_by=datetime.fromisoformat("2026-09-09T09:00:00.500000+08:00"),
        )
        == newer_date
    )
    assert (
        repository.latest_metric_date_as_of(
            "store-1", newer_date, collected_by=removed_at
        )
        == old_date
    )


def test_metric_history_isolated_by_store_and_grain_at_same_observation_time(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    observed_at = datetime(2026, 9, 8, 9, tzinfo=UTC)
    campaign = _metric(
        data_date=target,
        collected_at=observed_at,
        grain=MetricGrain.CAMPAIGN,
        item_id="shared-key",
        spend=Decimal(10),
    )
    item = replace(campaign, grain=MetricGrain.ITEM, spend=Decimal(999))
    other_store = replace(campaign, store_id="store-2", spend=Decimal(888))
    repository.upsert_metric(campaign)
    repository.upsert_metric(item)
    repository.upsert_metric(other_store)

    replayed = repository.list_campaign_metrics_between(
        "store-1", "campaign-1", target, target, collected_by=observed_at
    )

    assert replayed == (campaign,)
    assert set(repository.list_metrics("store-1", "campaign-1", target)) == {
        campaign,
        item,
    }


def test_late_arriving_older_observation_does_not_roll_back_current_metric(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    newer = _metric(
        data_date=target,
        collected_at=datetime(2026, 9, 9, 9, tzinfo=UTC),
        grain=MetricGrain.CAMPAIGN,
        item_id="__ALL__",
        spend=Decimal(20),
    )
    older = replace(
        newer,
        collected_at=datetime(2026, 9, 8, 9, tzinfo=UTC),
        spend=Decimal(10),
    )
    repository.upsert_metric(newer)
    repository.upsert_metric(older)

    assert repository.list_metrics("store-1", "campaign-1", target) == (newer,)
    assert repository.list_campaign_metrics_between(
        "store-1",
        "campaign-1",
        target,
        target,
        collected_by=datetime(2026, 9, 8, 10, tzinfo=UTC),
    ) == (older,)


def test_subsecond_order_and_cutoff_use_exact_utc_microseconds(repository):
    target = date(2026, 9, 7)
    early = _metric(
        data_date=target,
        grain=MetricGrain.CAMPAIGN,
        item_id="__ALL__",
        spend=Decimal(10),
        collected_at=datetime(2026, 9, 8, 9, 0, 0, 100000, tzinfo=UTC),
    )
    late = replace(
        early,
        spend=Decimal(20),
        collected_at=datetime(2026, 9, 8, 9, 0, 0, 900000, tzinfo=UTC),
    )
    repository.upsert_metric(late)
    repository.upsert_metric(early)

    assert repository.list_metrics("store-1", "campaign-1", target) == (late,)
    assert repository.list_campaign_metrics_between(
        "store-1",
        "campaign-1",
        target,
        target,
        collected_by=datetime(2026, 9, 8, 9, 0, 0, 500000, tzinfo=UTC),
    ) == (early,)


def test_equivalent_mixed_offsets_share_exact_observation_instant(repository):
    target = date(2026, 9, 7)
    utc_value = _metric(
        data_date=target,
        grain=MetricGrain.CAMPAIGN,
        item_id="__ALL__",
        spend=Decimal(10),
        collected_at=datetime.fromisoformat("2026-09-08T09:00:00.900000+00:00"),
    )
    offset_value = replace(
        utc_value,
        spend=Decimal(20),
        collected_at=datetime.fromisoformat("2026-09-08T10:00:00.900000+01:00"),
    )
    repository.upsert_metric(utc_value)
    repository.upsert_metric(offset_value)

    current = repository.list_metrics("store-1", "campaign-1", target)
    assert current[0].spend == Decimal(20)
    with repository.database.connect() as connection:
        values = connection.execute(
            """SELECT DISTINCT collected_at_us
            FROM optimizer_ad_metric_observations
            WHERE store_id='store-1' AND campaign_id='campaign-1'"""
        ).fetchall()
    assert len(values) == 1


def test_replace_store_day_records_tombstone_for_disappeared_metric(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    first_seen = datetime(2026, 9, 8, 9, tzinfo=UTC)
    replacement_time = datetime(2026, 9, 9, 9, tzinfo=UTC)
    retained = _metric(
        data_date=target,
        collected_at=first_seen,
        grain=MetricGrain.CAMPAIGN,
        item_id="__ALL__",
    )
    disappeared = replace(
        retained,
        campaign_id="campaign-gone",
        campaign_name="已消失",
    )
    repository.replace_store_day(
        "store-1", target, (retained, disappeared), (), "success"
    )
    repository.replace_store_day(
        "store-1",
        target,
        (replace(retained, collected_at=replacement_time),),
        (),
        "success",
    )

    before = repository.list_campaign_metrics_between(
        "store-1",
        "campaign-gone",
        target,
        target,
        collected_by=datetime(2026, 9, 8, 10, tzinfo=UTC),
    )
    after = repository.list_campaign_metrics_between(
        "store-1",
        "campaign-gone",
        target,
        target,
        collected_by=datetime(2026, 9, 9, 10, tzinfo=UTC),
    )

    assert before == (disappeared,)
    assert after == ()


def test_older_replacement_batch_cannot_roll_back_or_tombstone_newer_facts(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    newer_time = datetime(2026, 9, 9, 9, tzinfo=UTC)
    older_time = datetime(2026, 9, 8, 9, tzinfo=UTC)
    retained_new = _metric(
        data_date=target,
        item_id="retained",
        grain=MetricGrain.CAMPAIGN,
        spend=Decimal(20),
        collected_at=newer_time,
    )
    new_only = replace(
        retained_new,
        campaign_id="new-only",
        item_id="new-only",
        spend=Decimal(30),
    )
    repository.replace_store_day(
        "store-1", target, (retained_new, new_only), (), "success"
    )
    retained_old = replace(retained_new, spend=Decimal(10), collected_at=older_time)

    repository.replace_store_day("store-1", target, (retained_old,), (), "success")

    assert repository.list_metrics("store-1", "campaign-1", target) == (retained_new,)
    assert repository.list_metrics("store-1", "new-only", target) == (new_only,)


def test_campaign_totals_exclude_overview_and_item_grains(
    repository: OptimizerRepository,
):
    overview = _metric(
        campaign_id="__OVERVIEW__",
        item_id="__ALL__",
        grain=MetricGrain.OVERVIEW,
        spend=Decimal(100),
        gmv=Decimal(300),
        orders=3,
    )
    campaign = _metric(
        item_id="__ALL__",
        grain=MetricGrain.CAMPAIGN,
        spend=Decimal(100),
        gmv=Decimal(300),
        orders=3,
    )
    item = _metric(
        item_id="item-1",
        grain=MetricGrain.ITEM,
        spend=Decimal(100),
        gmv=Decimal(300),
        orders=3,
    )
    for metric in (overview, campaign, item):
        repository.upsert_metric(metric)

    assert repository.campaign_totals("store-1", date(2026, 9, 7)) == (
        Decimal(100),
        Decimal(300),
        3,
    )


def test_campaign_totals_sum_text_decimals_without_float_rounding(
    repository: OptimizerRepository,
):
    for index, spend in enumerate(("0.10", "0.20", "999999999999999.99"), start=1):
        repository.upsert_metric(
            _metric(
                campaign_id=f"campaign-{index}",
                item_id="__ALL__",
                grain=MetricGrain.CAMPAIGN,
                spend=Decimal(spend),
                gmv=Decimal(spend),
                orders=1,
            )
        )

    assert repository.campaign_totals("store-1", date(2026, 9, 7)) == (
        Decimal("1000000000000000.29"),
        Decimal("1000000000000000.29"),
        3,
    )


def test_profiles_keep_immutable_versions(repository: OptimizerRepository):
    profile = CampaignProfile(
        "store-1",
        "campaign-1",
        ProfileStatus.FORMAL,
        date(2026, 9, 1),
        Decimal(2),
        Decimal("0.05"),
        Decimal("0.5"),
        Decimal("0.2"),
        Decimal("0.1"),
        3,
        3,
        7,
        3,
        frozenset({Action.INCREASE_BUDGET}),
    )

    repository.save_profile(profile)
    current = repository.save_profile(replace(profile, minimum_roas=Decimal("2.5")))

    assert current.minimum_roas == Decimal("2.5")
    assert [
        item.minimum_roas
        for item in repository.list_profile_versions("store-1", "campaign-1")
    ] == [Decimal(2), Decimal("2.5")]

    restored = repository.save_profile(replace(profile, note="恢复旧决策"))
    assert restored.minimum_roas == Decimal(2)
    assert [
        item.minimum_roas
        for item in repository.list_profile_versions("store-1", "campaign-1")
    ] == [Decimal(2), Decimal("2.5"), Decimal(2)]


def test_campaign_read_models_union_sources_and_freeze_as_of(
    repository: OptimizerRepository,
) -> None:
    cutoff = datetime(2026, 9, 10, 4, tzinfo=UTC)
    target = date(2026, 9, 9)
    repository.upsert_metric(
        _metric(
            campaign_id="metric-only",
            campaign_name="指标活动",
            item_id="__ALL__",
            grain=MetricGrain.CAMPAIGN,
            data_date=target,
            spend=Decimal(10),
            gmv=Decimal(30),
            orders=3,
            collected_at=datetime(2026, 9, 10, 3, tzinfo=UTC),
        )
    )
    repository.upsert_metric(
        _metric(
            campaign_id="future-only",
            campaign_name="未来指标",
            item_id="__ALL__",
            grain=MetricGrain.CAMPAIGN,
            data_date=target,
            collected_at=datetime(2026, 9, 10, 5, tzinfo=UTC),
        )
    )
    repository.save_snapshot(
        CampaignSnapshot(
            "store-1", "snapshot-only", "快照活动", "product",
            CampaignStatus.ACTIVE, Decimal(100), Decimal("2.5"), "THB",
            datetime(2026, 9, 10, 3, tzinfo=UTC), target,
        )
    )
    profile = CampaignProfile(
        "store-1", "profile-only", ProfileStatus.FORMAL, target,
        Decimal(2), Decimal("0.05"), Decimal("0.5"), Decimal("0.2"),
        Decimal("0.1"), 3, 2, 7, 3, frozenset({Action.HOLD}), "第一版",
    )
    repository.save_profile(profile)
    repository.save_profile(
        replace(profile, note="未来版", effective_from=date(2026, 9, 11))
    )
    repository.upsert_metric(
        _metric(
            store_id="other-store",
            campaign_id="other",
            item_id="__ALL__",
            grain=MetricGrain.CAMPAIGN,
            data_date=target,
            collected_at=datetime(2026, 9, 10, 3, tzinfo=UTC),
        )
    )

    records = repository.campaign_read_models_as_of(
        "store-1",
        recent_start=date(2026, 9, 3),
        data_through=target,
        decision_at=cutoff,
    )

    assert [item.campaign_id for item in records] == [
        "metric-only", "profile-only", "snapshot-only"
    ]
    assert records[0].metric is not None and records[0].metric.spend == Decimal(10)
    assert records[1].profile is not None and records[1].profile.note == "第一版"
    assert len(records[1].profile_versions) == 1
    assert records[2].snapshot is not None


def test_backdated_profiles_keep_current_and_allocate_monotonic_versions(
    repository: OptimizerRepository,
) -> None:
    current = CampaignProfile(
        "store-1", "campaign-1", ProfileStatus.FORMAL, date(2026, 9, 10),
        Decimal(2), Decimal("0.05"), Decimal("0.5"), Decimal("0.2"),
        Decimal("0.1"), 3, 2, 7, 3, frozenset({Action.HOLD}), "当前版",
    )
    repository.save_profile(current)
    repository.save_profile(
        replace(current, effective_from=date(2026, 9, 9), minimum_roas=Decimal(3))
    )
    repository.save_profile(
        replace(current, effective_from=date(2026, 9, 8), minimum_roas=Decimal(4))
    )

    versions = repository.list_profile_versions("store-1", "campaign-1")
    record = repository.campaign_read_models_as_of(
        "store-1",
        recent_start=date(2026, 9, 3),
        data_through=date(2026, 9, 10),
        decision_at=datetime(2026, 9, 10, 4, tzinfo=UTC),
    )[0]

    assert [item.minimum_roas for item in versions] == [Decimal(2), Decimal(3), Decimal(4)]
    assert repository.get_current_profile("store-1", "campaign-1") == current
    assert record.profile == current
    assert record.profile_versions == versions


def test_campaign_metric_read_query_uses_bounded_indexes_without_temp_sort(
    repository: OptimizerRepository,
) -> None:
    cutoff = int(datetime(2026, 9, 10, 4, tzinfo=UTC).timestamp() * 1_000_000)
    with repository.database.connect() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN " + repository_module._CAMPAIGN_METRICS_AS_OF_SQL,
            (
                "store-1", "2026-09-03", "2026-09-10", cutoff, cutoff,
                "2026-09-03", "2026-09-10", cutoff, cutoff,
            ),
        ).fetchall()

    detail = "\n".join(row["detail"] for row in plan)
    assert "optimizer_observation_campaign_recent" in detail
    assert "optimizer_observation_logical_latest" in detail
    assert "data_date>? AND data_date<?" in detail
    assert "USE TEMP B-TREE" not in detail
    assert "MATERIALIZE" not in detail


def test_campaign_metric_read_model_excludes_large_append_history_before_recent_start(
    repository: OptimizerRepository,
) -> None:
    old_day = date(2026, 8, 1)
    for revision in range(250):
        repository.upsert_metric(
            _metric(
                campaign_id="old-only",
                item_id="__ALL__",
                grain=MetricGrain.CAMPAIGN,
                data_date=old_day,
                spend=Decimal(revision),
                collected_at=datetime(
                    2026, 8, 2, 0, 0, 0, revision, tzinfo=UTC
                ),
            )
        )
    repository.upsert_metric(
        _metric(
            campaign_id="recent",
            item_id="__ALL__",
            grain=MetricGrain.CAMPAIGN,
            data_date=date(2026, 9, 9),
            spend=Decimal(9),
            collected_at=datetime(2026, 9, 10, 3, tzinfo=UTC),
        )
    )

    records = repository.campaign_read_models_as_of(
        "store-1",
        recent_start=date(2026, 9, 3),
        data_through=date(2026, 9, 10),
        decision_at=datetime(2026, 9, 10, 4, tzinfo=UTC),
    )

    assert [record.campaign_id for record in records] == ["recent"]
    assert records[0].metric is not None
    assert records[0].metric.spend == Decimal(9)


def test_snapshot_is_idempotent_by_observed_at(repository: OptimizerRepository):
    snapshot = CampaignSnapshot(
        "store-1",
        "campaign-1",
        "秋季",
        "auto",
        CampaignStatus.ACTIVE,
        Decimal(100),
        Decimal(2),
        "CNY",
        _now(),
        date(2026, 9, 7),
    )

    assert repository.save_snapshot(snapshot) == snapshot
    replaced = repository.save_snapshot(replace(snapshot, current_budget=Decimal(120)))

    assert repository.get_snapshot("store-1", "campaign-1", _now()) == replaced
    assert repository.list_snapshots("store-1", "campaign-1") == (replaced,)
    assert (
        replaced.current_budget,
        replaced.target_roas,
        replaced.status,
        replaced.observed_at,
    ) == (
        Decimal(120),
        Decimal(2),
        CampaignStatus.ACTIVE,
        _now(),
    )


def test_snapshot_round_trips_missing_budget_and_target_roas_as_sql_null(
    repository: OptimizerRepository,
):
    snapshot = CampaignSnapshot(
        "store-1",
        "campaign-missing-budget",
        "秋季",
        "auto",
        CampaignStatus.ACTIVE,
        None,
        None,
        "CNY",
        _now(),
        date(2026, 9, 7),
    )

    assert repository.save_snapshot(snapshot) == snapshot
    assert (
        repository.get_snapshot("store-1", "campaign-missing-budget", _now())
        == snapshot
    )
    with repository.database.connect() as connection:
        row = connection.execute(
            "SELECT current_budget, target_roas FROM optimizer_campaign_snapshots "
            "WHERE campaign_id='campaign-missing-budget'"
        ).fetchone()
    assert tuple(row) == (None, None)


def test_snapshot_round_trips_unknown_campaign_status(repository: OptimizerRepository):
    snapshot = CampaignSnapshot(
        "store-1",
        "campaign-unknown",
        "秋季",
        "auto",
        CampaignStatus.UNKNOWN,
        None,
        None,
        "CNY",
        _now(),
        date(2026, 9, 7),
    )

    assert repository.save_snapshot(snapshot) == snapshot
    assert repository.get_snapshot("store-1", "campaign-unknown", _now()) == snapshot


def test_recommendation_idempotence_and_content_change_creates_revision(
    repository: OptimizerRepository,
):
    initial = repository.save_recommendation(_recommendation())
    same = repository.save_recommendation(_recommendation(recommendation_id="other"))
    changed = repository.save_recommendation(
        _recommendation(recommendation_id="rec-new", input_hash="input-2")
    )
    content_changed = repository.save_recommendation(
        _recommendation(
            recommendation_id="rec-content",
            proposed_value=Decimal(130),
            change_ratio=Decimal("0.3"),
        )
    )

    assert same == initial
    assert (initial.revision, changed.revision, content_changed.revision) == (1, 2, 3)
    assert [
        item.revision
        for item in repository.list_recommendations(
            "store-1", "campaign-1", date(2026, 9, 7)
        )
    ] == [1, 2, 3]


def test_concurrent_connections_allocate_distinct_recommendation_revisions(
    tmp_path: Path,
):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    first = OptimizerRepository(database)
    second = OptimizerRepository(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved = tuple(
            executor.map(
                lambda item: (first if item == "a" else second).save_recommendation(
                    _recommendation(input_hash=item)
                ),
                ("a", "b"),
            )
        )

    assert {item.revision for item in saved} == {1, 2}


def test_primary_slot_keeps_latest_action_and_structure_slots_remain_independent(
    repository,
):
    increase = repository.save_recommendation(_recommendation())
    decrease = repository.save_recommendation(
        replace(
            _recommendation(recommendation_id="rec-decrease", input_hash="decrease"),
            action=Action.DECREASE_BUDGET,
            proposed_value=Decimal(80),
            change_ratio=Decimal("-0.2"),
        )
    )
    create = repository.save_recommendation(
        _recommendation(
            recommendation_id="rec-create",
            input_hash="create",
            action=Action.CREATE_GROUP,
            current_value=None,
            proposed_value=None,
            change_ratio=None,
            slot=RecommendationSlot.CREATE_GROUP,
        )
    )
    split = repository.save_recommendation(
        _recommendation(
            recommendation_id="rec-split",
            input_hash="split",
            action=Action.SPLIT_GROUP,
            current_value=None,
            proposed_value=None,
            change_ratio=None,
            slot=RecommendationSlot.SPLIT_GROUP,
        )
    )

    all_versions = repository.list_recommendations(
        "store-1", "campaign-1", date(2026, 9, 7)
    )
    latest = repository.list_recommendations(
        "store-1", "campaign-1", date(2026, 9, 7), latest_only=True
    )

    assert (increase.revision, decrease.revision) == (1, 2)
    assert {item.recommendation_id for item in all_versions} == {
        increase.recommendation_id,
        decrease.recommendation_id,
        create.recommendation_id,
        split.recommendation_id,
    }
    assert {item.recommendation_id for item in latest} == {
        decrease.recommendation_id,
        create.recommendation_id,
        split.recommendation_id,
    }


def test_feedback_and_evaluation_round_trip(repository: OptimizerRepository):
    recommendation = repository.save_recommendation(_recommendation())
    feedback = Feedback(
        "feedback-1",
        recommendation.recommendation_id,
        FeedbackStatus.EXECUTED,
        None,
        _now(),
        "已执行",
    )
    evaluation = Evaluation(
        "evaluation-1",
        recommendation.recommendation_id,
        EvaluationStatus.EFFECTIVE,
        date(2026, 8, 25),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 7),
        "v1",
        _now(),
        _window(date(2026, 8, 25), date(2026, 8, 31)),
        _window(),
        feedback_id=feedback.feedback_id,
        applied_value=recommendation.proposed_value,
        input_hash="evaluation-input",
    )

    assert repository.save_feedback("store-1", feedback) == feedback
    assert repository.save_feedback("store-1", feedback) == feedback
    assert repository.save_evaluation("store-1", evaluation) == evaluation
    assert repository.list_feedback("store-1", recommendation.recommendation_id) == (
        feedback,
    )
    assert repository.list_evaluations("store-1", recommendation.recommendation_id) == (
        evaluation,
    )


def test_review_read_models_aggregate_exact_action_as_of_and_detect_rollback(
    repository: OptimizerRepository,
) -> None:
    recommendation = repository.save_recommendation(_recommendation())
    feedback = Feedback(
        "feedback-1", recommendation.recommendation_id, FeedbackStatus.EXECUTED,
        None, datetime(2026, 9, 10, 1, tzinfo=UTC), "已执行",
        recommendation.revision,
    )
    insufficient_feedback = replace(
        feedback,
        feedback_id="feedback-2",
        executed_at=datetime(2026, 9, 10, 2, tzinfo=UTC),
    )
    future_feedback = replace(
        feedback,
        feedback_id="feedback-future",
        executed_at=datetime(2026, 9, 10, 5, tzinfo=UTC),
    )
    for item in (feedback, insufficient_feedback, future_feedback):
        repository.save_feedback("store-1", item)
    baseline = _window(date(2026, 8, 25), date(2026, 8, 31))
    after = _window(date(2026, 9, 1), date(2026, 9, 7))
    common = {
        "recommendation_id": recommendation.recommendation_id,
        "baseline_start": baseline.start_date,
        "baseline_end": baseline.end_date,
        "observation_start": after.start_date,
        "observation_end": after.end_date,
        "algorithm_version": "v1",
        "baseline_stats": baseline,
        "after_stats": after,
        "recommendation_revision": recommendation.revision,
        "applied_value": recommendation.proposed_value,
        "input_dates": (after.end_date,),
    }
    repository.save_evaluation(
        "store-1",
        Evaluation(
            "evaluation-old", status=EvaluationStatus.PENDING,
            evaluated_at=datetime(2026, 9, 10, 2, tzinfo=UTC),
            feedback_id=feedback.feedback_id, input_hash="old", **common,
        ),
    )
    repository.save_evaluation(
        "store-1",
        Evaluation(
            "evaluation-latest", status=EvaluationStatus.EFFECTIVE,
            evaluated_at=datetime(2026, 9, 10, 3, tzinfo=UTC),
            feedback_id=feedback.feedback_id, input_hash="latest", **common,
        ),
    )
    repository.save_evaluation(
        "store-1",
        Evaluation(
            "evaluation-insufficient", status=EvaluationStatus.INSUFFICIENT_DATA,
            evaluated_at=datetime(2026, 9, 10, 3, tzinfo=UTC),
            feedback_id=insufficient_feedback.feedback_id,
            input_hash="insufficient", **common,
        ),
    )
    for day in (date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 7)):
        repository.upsert_metric(
            _metric(
                data_date=day,
                item_id="__ALL__",
                grain=MetricGrain.CAMPAIGN,
                spend=Decimal(10),
                gmv=Decimal(10),
                orders=1,
                collected_at=datetime(2026, 9, 10, 3, tzinfo=UTC),
            )
        )

    records = repository.review_read_models_as_of(
        "store-1", datetime(2026, 9, 10, 4, tzinfo=UTC)
    )

    assert [item.feedback.feedback_id for item in records] == [
        "feedback-1", "feedback-2"
    ]
    first = records[0]
    assert first.evaluation is not None
    assert first.evaluation.evaluation_id == "evaluation-latest"
    assert first.total_recommendations == 1
    assert first.feedback_count == 1
    assert first.adopted_count == 1
    assert first.completed_evaluation_count == 1
    assert first.effective_count == 1
    assert first.rollback_triggered is True
    assert records[1].completed_evaluation_count == 1
    assert records[1].rollback_triggered is None


def test_evaluation_requires_executed_feedback_on_exact_recommendation_revision(
    repository: OptimizerRepository,
):
    recommendation = repository.save_recommendation(_recommendation())
    window = _window()
    evaluation = Evaluation(
        "evaluation-without-feedback",
        recommendation.recommendation_id,
        EvaluationStatus.EFFECTIVE,
        window.start_date,
        window.end_date,
        date(2026, 9, 8),
        date(2026, 9, 14),
        "v1",
        _now(),
        window,
        _window(date(2026, 9, 8), date(2026, 9, 14)),
        recommendation_revision=recommendation.revision,
        feedback_id="missing-feedback",
        applied_value=recommendation.proposed_value,
        input_hash="evaluation-input",
    )

    with pytest.raises(ValueError, match="executed feedback"):
        repository.save_evaluation("store-1", evaluation)


def test_collection_day_records_and_reports_gaps(repository: OptimizerRepository):
    assert (
        repository.save_run_log(
            RunLog("run-1", "no_data", _now(), details={"source": "export"})
        ).status
        == "no_data"
    )
    repository.upsert_metric(_metric(data_date=date(2026, 9, 1)))
    repository.record_collection_day("store-1", date(2026, 9, 1), "success")
    repository.record_collection_day("store-1", date(2026, 9, 2), "failed")
    repository.record_collection_day("store-1", date(2026, 9, 3), "no_data")

    assert repository.collection_gaps(
        "store-1", date(2026, 9, 1), date(2026, 9, 4)
    ) == (date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4))


def test_record_collection_failure_preserves_existing_success(
    repository: OptimizerRepository,
):
    successful_day = date(2026, 9, 7)
    failed_day = date(2026, 9, 8)
    repository.upsert_metric(_metric(data_date=successful_day))
    repository.record_collection_day("store-1", successful_day, "success")

    repository.record_collection_failure("store-1", successful_day, note="page loop")
    repository.record_collection_failure("store-1", failed_day, note="page loop")

    assert repository.collection_gaps("store-1", successful_day, failed_day) == (
        failed_day,
    )
    with repository.database.connect() as connection:
        assert (
            connection.execute(
                "SELECT status FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
                ("store-1", successful_day.isoformat()),
            ).fetchone()[0]
            == "success"
        )


@pytest.mark.parametrize("reserved_note", ["claim", "__optimizer_collection_claim__"])
@pytest.mark.parametrize(
    "method", ["record_collection_day", "record_collection_failure"]
)
def test_collection_result_rejects_reserved_claim_notes(
    repository: OptimizerRepository, reserved_note: str, method: str
):
    target = date(2026, 9, 7)

    with pytest.raises(ValueError, match="reserved"):
        if method == "record_collection_day":
            repository.record_collection_day(
                "store-1", target, "failed", note=reserved_note
            )
        else:
            repository.record_collection_failure("store-1", target, note=reserved_note)

    assert repository.get_collection_day("store-1", target) is None


def test_claim_uses_reserved_sentinel_without_creating_result_event(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    assert repository.claim_collection_day(
        "store-1", target, "owner", datetime(2099, 9, 7, tzinfo=UTC)
    )
    repository.release_collection_claim("store-1", target, "owner")

    current = repository.get_collection_day("store-1", target)
    assert current is not None and current.status == "failed"
    assert (
        repository.get_collection_day_as_of(
            "store-1", target, datetime(2099, 9, 8, tzinfo=UTC)
        )
        is None
    )
    assert repository.collection_gaps("store-1", target, target) == (target,)
    with repository.database.connect() as connection:
        row = connection.execute(
            "SELECT note FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
            ("store-1", target.isoformat()),
        ).fetchone()
        event_count = connection.execute(
            "SELECT COUNT(*) FROM optimizer_collection_day_events"
        ).fetchone()[0]
    assert row["note"] == "__optimizer_collection_claim__"
    assert event_count == 0


def test_normal_collection_failure_is_preserved_as_event(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    repository.record_collection_failure(
        "store-1",
        target,
        note="page loop",
        attempted_at=datetime(2026, 9, 7, 9, tzinfo=UTC),
    )

    replayed = repository.get_collection_day_as_of(
        "store-1", target, datetime(2026, 9, 7, 10, tzinfo=UTC)
    )
    assert replayed is not None
    assert replayed.status == "failed"


def test_collection_day_events_replay_subsecond_mixed_offset_and_store_isolation(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    first = datetime(2026, 9, 7, 8, 59, 59, 900_000, tzinfo=UTC)
    equivalent_offset = datetime.fromisoformat("2026-09-07T16:59:59.900000+08:00")
    later = datetime(2026, 9, 7, 9, 0, 0, 100_000, tzinfo=UTC)
    repository.record_collection_day("store-1", target, "failed", attempted_at=first)
    repository.record_collection_day(
        "store-1", target, "no_data", attempted_at=equivalent_offset
    )
    repository.record_collection_day("store-2", target, "failed", attempted_at=later)
    repository.upsert_metric(_metric(data_date=target, collected_at=later))
    repository.record_collection_day("store-1", target, "success", attempted_at=later)

    at_same_instant = repository.get_collection_day_as_of("store-1", target, first)
    before_later = repository.get_collection_day_as_of(
        "store-1",
        target,
        datetime(2026, 9, 7, 8, 59, 59, 950_000, tzinfo=UTC),
    )
    after_later = repository.get_collection_day_as_of("store-1", target, later)

    assert at_same_instant is not None and at_same_instant.status == "no_data"
    assert before_later is not None and before_later.status == "no_data"
    assert after_later is not None and after_later.status == "success"
    assert repository.get_collection_day_as_of("missing", target, later) is None


def test_rejected_failure_after_success_is_not_appended_as_current_event(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 7)
    success_at = datetime(2026, 9, 7, 9, tzinfo=UTC)
    repository.upsert_metric(_metric(data_date=target, collected_at=success_at))
    repository.record_collection_day(
        "store-1", target, "success", attempted_at=success_at
    )

    repository.record_collection_failure(
        "store-1",
        target,
        note="later failure",
        attempted_at=datetime(2026, 9, 7, 10, tzinfo=UTC),
    )

    replayed = repository.get_collection_day_as_of(
        "store-1", target, datetime(2026, 9, 7, 11, tzinfo=UTC)
    )
    assert replayed is not None and replayed.status == "success"
    with repository.database.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM optimizer_collection_day_events "
                "WHERE store_id=? AND data_date=?",
                ("store-1", target.isoformat()),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("status", ["success", "failed", "no_data"])
def test_repository_accepts_collection_outcome_statuses(
    repository: OptimizerRepository, status: str
):
    assert (
        repository.save_run_log(RunLog(f"run-{status}", status, _now())).status
        == status
    )
    if status == "success":
        repository.upsert_metric(_metric())
    repository.record_collection_day("store-1", date(2026, 9, 7), status)


def test_repository_rejects_unknown_collection_outcome_status(
    repository: OptimizerRepository,
):
    with pytest.raises(ValueError, match="collection status"):
        repository.save_run_log(RunLog("bad", "running", _now()))
    with pytest.raises(ValueError, match="collection status"):
        repository.record_collection_day("store-1", date(2026, 9, 7), "running")


@pytest.mark.parametrize(
    "method",
    [
        "list_metrics",
        "campaign_totals",
        "has_successful_collection",
        "get_collection_day",
    ],
)
def test_repository_rejects_datetime_where_business_date_is_required(
    repository, method
):
    with pytest.raises(TypeError, match="date"):
        if method == "list_metrics":
            getattr(repository, method)(
                "store-1", "campaign-1", datetime(2026, 9, 7, tzinfo=UTC)
            )
        else:
            getattr(repository, method)("store-1", datetime(2026, 9, 7, tzinfo=UTC))


def test_replace_store_day_rejects_non_success_and_empty_metrics(repository):
    with pytest.raises(ValueError, match="only accepts success"):
        repository.replace_store_day("store-1", date(2026, 9, 7), (), (), "failed")
    with pytest.raises(ValueError, match="requires metrics"):
        repository.replace_store_day("store-1", date(2026, 9, 7), (), (), "success")


def test_replace_store_day_rejects_snapshot_from_another_business_day(repository):
    target = date(2026, 9, 7)
    snapshot = CampaignSnapshot(
        "store-1",
        "campaign-1",
        "秋季",
        "auto",
        CampaignStatus.ACTIVE,
        Decimal(100),
        Decimal(2),
        "CNY",
        _now(),
        date(2026, 9, 8),
    )

    with pytest.raises(ValueError, match="snapshots"):
        repository.replace_store_day(
            "store-1", target, (_metric(data_date=target),), (snapshot,), "success"
        )

    assert repository.list_metrics("store-1", "campaign-1", target) == ()


def test_collection_success_requires_fact_and_cannot_be_downgraded(repository):
    target = date(2026, 9, 7)
    with pytest.raises(ValueError, match="persisted metrics"):
        repository.record_collection_day("store-1", target, "success")
    repository.upsert_metric(_metric(data_date=target))
    repository.record_collection_day("store-1", target, "success")
    repository.record_collection_day("store-1", target, "failed")
    assert repository.has_successful_collection("store-1", target)


def test_expired_claim_is_fenced_after_new_owner_writes(repository):
    target = date(2026, 9, 7)
    old = _metric(data_date=target, spend=Decimal(1))
    new = _metric(data_date=target, spend=Decimal(99))
    assert repository.claim_collection_day(
        "store-1", target, "owner-a", datetime(2026, 9, 1, tzinfo=UTC)
    )
    assert repository.claim_collection_day(
        "store-1", target, "owner-b", datetime(2026, 9, 30, tzinfo=UTC)
    )
    repository.replace_store_day(
        "store-1", target, (new,), (), "success", claim_token="owner-b"
    )
    with pytest.raises(ClaimLost):
        repository.replace_store_day(
            "store-1", target, (old,), (), "success", claim_token="owner-a"
        )
    assert repository.list_metrics("store-1", "campaign-1", target)[0].spend == Decimal(
        99
    )


def test_run_log_details_round_trip_supported_value_types(
    repository: OptimizerRepository,
):
    log = RunLog(
        "run-details",
        "success",
        _now(),
        details={
            "amount": Decimal("1.20"),
            "date": date(2026, 9, 9),
            "at": _now(),
            "action": Action.HOLD,
            "confidence": Confidence.HIGH,
            "feedback": FeedbackStatus.EXECUTED,
            "evaluation": EvaluationStatus.EFFECTIVE,
            "quality": DataQualityStatus.HEALTHY,
            "profile": ProfileStatus.FORMAL,
            "campaign": CampaignStatus.ACTIVE,
        },
    )

    assert repository.save_run_log(log) == log
    restored = repository.get_run_log("run-details")
    assert restored == log
    assert restored is not None
    assert restored.details["action"] is Action.HOLD
    assert restored.details["confidence"] is Confidence.HIGH
    assert restored.details["feedback"] is FeedbackStatus.EXECUTED
    assert restored.details["evaluation"] is EvaluationStatus.EFFECTIVE
    assert restored.details["quality"] is DataQualityStatus.HEALTHY
    assert restored.details["profile"] is ProfileStatus.FORMAL
    assert restored.details["campaign"] is CampaignStatus.ACTIVE


def test_store_identity_is_required_for_feedback_and_evaluation(
    repository: OptimizerRepository,
):
    first = repository.save_recommendation(_recommendation())
    second = repository.save_recommendation(
        _recommendation(
            store_id="store-2", recommendation_id="rec-store-2", input_hash="other"
        )
    )
    feedback = Feedback(
        "feedback-store-2",
        second.recommendation_id,
        FeedbackStatus.EXECUTED,
        None,
        _now(),
    )

    with pytest.raises(ValueError, match="recommendation_id"):
        repository.save_feedback("store-1", feedback)
    assert repository.save_feedback("store-2", feedback) == feedback
    assert repository.list_feedback("store-1", first.recommendation_id) == ()


def test_cross_store_duplicate_feedback_and_evaluation_ids_raise_conflict(
    repository: OptimizerRepository,
):
    first = repository.save_recommendation(_recommendation())
    second = repository.save_recommendation(
        _recommendation(store_id="store-2", input_hash="store-2")
    )
    first_feedback = Feedback(
        "shared-feedback",
        first.recommendation_id,
        FeedbackStatus.EXECUTED,
        None,
        _now(),
    )
    second_feedback = Feedback(
        "shared-feedback",
        second.recommendation_id,
        FeedbackStatus.EXECUTED,
        None,
        _now(),
    )
    first_evaluation = Evaluation(
        "shared-evaluation",
        first.recommendation_id,
        EvaluationStatus.EFFECTIVE,
        date(2026, 8, 25),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 7),
        "v1",
        _now(),
        _window(date(2026, 8, 25), date(2026, 8, 31)),
        _window(),
        feedback_id=first_feedback.feedback_id,
        applied_value=first.proposed_value,
        input_hash="shared-evaluation-input",
    )
    second_evaluation = replace(
        first_evaluation, recommendation_id=second.recommendation_id
    )

    repository.save_feedback("store-1", first_feedback)
    repository.save_evaluation("store-1", first_evaluation)

    with pytest.raises(ValueError, match="feedback_id already belongs"):
        repository.save_feedback("store-2", second_feedback)
    with pytest.raises(ValueError, match="evaluation_id already belongs"):
        repository.save_evaluation("store-2", second_evaluation)

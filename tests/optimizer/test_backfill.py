from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from adwatch.optimizer.collector import CollectionResult
from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.models import (
    AdMetric,
    CampaignSnapshot,
    CampaignStatus,
    DataQuality,
    DataQualityStatus,
)
from adwatch.optimizer.repository import ClaimLost, OptimizerRepository

AS_OF = date(2026, 9, 10)


def _quality() -> DataQuality:
    return DataQuality(DataQualityStatus.HEALTHY, datetime(2026, 9, 10, tzinfo=UTC))


def _metric(data_date: date) -> AdMetric:
    return AdMetric(
        "store-1",
        "campaign-1",
        "春季",
        "__ALL__",
        data_date,
        "CNY",
        Decimal(1),
        Decimal(2),
        1,
        datetime(2026, 9, 10, tzinfo=UTC),
        "fake",
    )


def _snapshot(data_date: date = AS_OF) -> CampaignSnapshot:
    return CampaignSnapshot(
        "store-1",
        "campaign-1",
        "春季",
        "auto",
        CampaignStatus.ACTIVE,
        Decimal(10),
        Decimal(2),
        "CNY",
        datetime(2026, 9, 10, tzinfo=UTC),
        data_date,
    )


@dataclass
class FakeCollector:
    outcomes: dict[date, object]

    def __post_init__(self) -> None:
        self.calls: list[date] = []

    def collect(self, data_date: date, **kwargs: object) -> CollectionResult:
        self.calls.append(data_date)
        outcome = self.outcomes.get(data_date, "success")
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "no_data":
            return CollectionResult((), (), _quality())
        if outcome == "incomplete":
            return CollectionResult(
                (_metric(data_date),),
                (_snapshot(data_date),),
                _quality(),
                complete=False,
            )
        return CollectionResult(
            (_metric(data_date),), (_snapshot(data_date),), _quality()
        )


@pytest.fixture
def repository(tmp_path: Path) -> OptimizerRepository:
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    return OptimizerRepository(database)


def test_first_run_requests_exactly_the_30_previous_dates_in_order(
    repository: OptimizerRepository,
):
    collector = FakeCollector({})
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(repository, collector, history_days=30).run(
        "store-1", AS_OF
    )

    expected = tuple(
        date.fromordinal(AS_OF.toordinal() - offset) for offset in range(30, 0, -1)
    )
    assert collector.calls == list(expected)
    assert result.requested_dates == expected
    assert result.successful_dates == expected
    assert result.no_data_dates == ()
    assert result.failed_dates == ()
    assert (result.metrics_count, result.snapshots_count) == (30, 30)
    assert (result.complete, result.partial, result.status) == (True, False, "complete")


def test_mixed_outcomes_are_isolated_and_errors_are_safe(
    repository: OptimizerRepository,
):
    failed = date(2026, 9, 4)
    no_data = date(2026, 9, 5)
    incomplete = date(2026, 9, 6)
    collector = FakeCollector(
        {
            failed: RuntimeError("api_key=secret-token"),
            no_data: "no_data",
            incomplete: "incomplete",
        }
    )
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(repository, collector, history_days=7).run(
        "store-1", AS_OF
    )

    assert collector.calls == [date(2026, 9, day) for day in range(3, 10)]
    assert result.failed_dates == (failed, incomplete)
    assert result.no_data_dates == (no_data,)
    assert len(result.successful_dates) == 4
    assert result.complete is False
    assert all("secret-token" not in str(error) for error in result.errors)
    assert result.errors[0].error_type == "collector_error"
    assert result.errors[1].error_type == "incomplete_collection"
    assert repository.collection_gaps("store-1", failed, failed) == (failed,)


def test_second_run_skips_success_and_non_due_no_data_but_retries_failed(
    repository: OptimizerRepository,
):
    old_success = date(2026, 9, 3)
    old_no_data = date(2026, 9, 4)
    failed = date(2026, 9, 5)
    repository.upsert_metric(_metric(old_success))
    repository.record_collection_day("store-1", old_success, "success")
    repository.record_collection_day(
        "store-1",
        old_no_data,
        "no_data",
        attempted_at=datetime(2026, 9, 9, tzinfo=UTC),
        next_retry_at=datetime(2026, 9, 16, tzinfo=UTC),
    )
    repository.record_collection_day("store-1", failed, "failed")
    collector = FakeCollector({})
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(
        repository, collector, history_days=7, no_data_recheck_days=7
    ).run("store-1", AS_OF)

    assert collector.calls == [date(2026, 9, day) for day in range(5, 10)]
    assert old_success not in result.requested_dates
    assert old_no_data not in result.requested_dates
    assert result.successful_dates == tuple(collector.calls)


def test_due_no_data_is_rechecked_and_new_success_replaces_no_data(
    repository: OptimizerRepository,
):
    target = date(2026, 9, 3)
    repository.record_collection_day(
        "store-1",
        target,
        "no_data",
        attempted_at=datetime(2026, 9, 1, tzinfo=UTC),
        next_retry_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    collector = FakeCollector({target: "success"})
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(
        repository, collector, history_days=7, no_data_recheck_days=7
    ).run("store-1", AS_OF)

    assert collector.calls == [target, *[date(2026, 9, day) for day in range(4, 10)]]
    assert target in result.successful_dates
    assert repository.has_successful_collection("store-1", target)


def test_invalid_dates_and_history_are_rejected(repository: OptimizerRepository):
    from adwatch.optimizer.backfill import BackfillService

    collector = FakeCollector({})
    with pytest.raises(ValueError):
        BackfillService(repository, collector, history_days=0)
    with pytest.raises(TypeError):
        BackfillService(repository, collector, history_days=1).run(
            "store-1", datetime(2026, 9, 10, tzinfo=UTC)
        )


def test_repeated_run_does_not_duplicate_metrics_or_snapshots(
    repository: OptimizerRepository,
):
    from adwatch.optimizer.backfill import BackfillService

    first = BackfillService(repository, FakeCollector({}), history_days=2).run(
        "store-1", AS_OF
    )
    collector = FakeCollector({})
    second = BackfillService(repository, collector, history_days=2).run(
        "store-1", AS_OF
    )

    assert first.metrics_count == 2
    assert second.requested_dates == ()
    assert collector.calls == []
    with repository.database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM optimizer_ad_metrics").fetchone()[
                0
            ]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM optimizer_campaign_snapshots"
            ).fetchone()[0]
            == 1
        )


def test_failed_retry_refreshes_attempted_at(repository: OptimizerRepository):
    target = date(2026, 9, 8)
    previous_attempt = datetime(2026, 9, 1, tzinfo=UTC)
    repository.record_collection_day(
        "store-1", target, "failed", attempted_at=previous_attempt
    )
    collector = FakeCollector({target: RuntimeError("temporary")})
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(repository, collector, history_days=2).run(
        "store-1", AS_OF
    )

    assert result.failed_dates == (target,)
    attempted_at = repository.get_collection_day("store-1", target).attempted_at
    assert attempted_at is not None
    assert attempted_at > previous_attempt


def test_failure_recording_error_is_safe_and_does_not_stop_next_date(
    repository: OptimizerRepository,
):
    first = date(2026, 9, 8)

    class FailureRecordingRepository(OptimizerRepository):
        def record_collection_failure(self, store_id, data_date, **kwargs):
            if data_date == first:
                raise RuntimeError("database secret")
            return super().record_collection_failure(store_id, data_date, **kwargs)

    failing_repository = FailureRecordingRepository(repository.database)
    collector = FakeCollector({first: RuntimeError("collector secret")})
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(failing_repository, collector, history_days=2).run(
        "store-1", AS_OF
    )

    assert collector.calls == [date(2026, 9, 8), date(2026, 9, 9)]
    assert result.failed_dates == (first,)
    assert result.successful_dates == (date(2026, 9, 9),)
    assert [error.error_type for error in result.errors] == [
        "collector_error",
        "persistence_failure",
    ]


def test_no_data_retry_compares_injected_business_timezone_boundary(repository):
    target = date(2026, 9, 9)
    repository.record_collection_day(
        "store-1",
        target,
        "no_data",
        attempted_at=datetime(2026, 9, 1, tzinfo=UTC),
        next_retry_at=datetime(2026, 9, 10, 23, 30, tzinfo=UTC),
    )
    collector = FakeCollector({})
    from adwatch.optimizer.backfill import BackfillService

    before_local_midnight = BackfillService(
        repository,
        collector,
        history_days=2,
        no_data_recheck_days=7,
        timezone=ZoneInfo("Asia/Shanghai"),
    ).run("store-1", date(2026, 9, 10))
    assert target not in before_local_midnight.requested_dates

    after_local_midnight = BackfillService(
        repository,
        collector,
        history_days=2,
        no_data_recheck_days=7,
        timezone=ZoneInfo("Asia/Shanghai"),
    ).run("store-1", date(2026, 9, 11))
    assert target in after_local_midnight.requested_dates


def test_two_services_atomically_claim_only_one_collection_call(repository):
    from adwatch.optimizer.backfill import BackfillService

    collector = FakeCollector({})
    services = [
        BackfillService(repository, collector, history_days=1) for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(lambda service: service.run("store-1", AS_OF), services)
        )

    assert collector.calls == [date(2026, 9, 9)]
    assert sum(len(result.successful_dates) for result in results) == 1


def test_expired_claim_can_be_recovered(repository):
    target = date(2026, 9, 9)
    assert repository.claim_collection_day(
        "store-1", target, "old-claim", datetime(2026, 9, 9, tzinfo=UTC)
    )
    collector = FakeCollector({})
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(repository, collector, history_days=1).run(
        "store-1", AS_OF
    )

    assert result.successful_dates == (target,)
    assert collector.calls == [target]


def test_backfill_deduplicates_metric_and_snapshot_logical_keys(repository):
    target = date(2026, 9, 9)

    class DuplicateCollector(FakeCollector):
        def collect(self, data_date, **kwargs):
            self.calls.append(data_date)
            metric = _metric(data_date)
            snapshot = _snapshot(data_date)
            return CollectionResult((metric, metric), (snapshot, snapshot), _quality())

    collector = DuplicateCollector({})
    from adwatch.optimizer.backfill import BackfillService

    result = BackfillService(repository, collector, history_days=1).run(
        "store-1", AS_OF
    )

    assert (result.metrics_count, result.snapshots_count) == (1, 1)
    with repository.database.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM optimizer_ad_metrics WHERE data_date=?",
                (target.isoformat(),),
            ).fetchone()[0]
            == result.metrics_count
        )


def test_backfill_passes_claim_token_to_collector(repository):
    from adwatch.optimizer.backfill import BackfillService

    class ClaimAwareCollector(FakeCollector):
        def collect(self, data_date, **kwargs):
            assert isinstance(kwargs.get("claim_token"), str)
            return super().collect(data_date, **kwargs)

    collector = ClaimAwareCollector({})
    result = BackfillService(repository, collector, history_days=1).run(
        "store-1", AS_OF
    )
    assert result.complete


def test_backfill_claim_lost_is_safe_partial_without_overwriting_facts(repository):
    from adwatch.optimizer.backfill import BackfillService

    target = date(2026, 9, 9)
    collector = FakeCollector({target: ClaimLost("stale owner")})
    result = BackfillService(repository, collector, history_days=1).run(
        "store-1", AS_OF
    )

    assert result.failed_dates == (target,)
    assert result.status == "partial"
    assert result.errors[0].error_type == "claim_lost"
    assert repository.list_metrics("store-1", "campaign-1", target) == ()

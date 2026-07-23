from dataclasses import dataclass
from datetime import date

from adwatch.collectors.base import Collector
from adwatch.pipeline.validation import validate_metric
from adwatch.storage.db import Database
from adwatch.storage.metrics import MetricRepository
from adwatch.storage.runs import RunRepository


@dataclass(frozen=True)
class PipelineSummary:
    run_id: str
    platform: str
    source: str
    received: int
    accepted: int
    quarantined: int


class PipelineRunner:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.runs = RunRepository(database)

    def run(self, collector: Collector, data_date: date) -> PipelineSummary:
        run_id = self.runs.start(collector.source, collector.platform.value)
        try:
            records = collector.collect(data_date)
            validated = [validate_metric(metric) for metric in records]
            valid = [result for result in validated if result.is_valid]
            invalid = [result for result in validated if not result.is_valid]
            with self.database.transaction() as connection:
                accepted = MetricRepository.upsert_many(
                    connection, [result.metric for result in valid]
                )
                self.runs.store_quality(connection, run_id, valid, invalid)
            self.runs.finish(
                run_id,
                received=len(records),
                accepted=accepted,
                quarantined=len(invalid),
            )
            return PipelineSummary(
                run_id=run_id,
                platform=collector.platform.value,
                source=collector.source,
                received=len(records),
                accepted=accepted,
                quarantined=len(invalid),
            )
        except Exception as error:
            self.runs.fail(run_id, error)
            raise

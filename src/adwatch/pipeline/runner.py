from dataclasses import dataclass
from datetime import date

from adwatch.collectors.base import Collector
from adwatch.domain import Platform
from adwatch.pipeline.validation import validate_metric
from adwatch.storage.db import Database
from adwatch.storage.metrics import (
    DEFAULT_RECONCILIATION_THRESHOLD,
    OVERVIEW_SKU,
    MetricRepository,
    ReconciliationResult,
)
from adwatch.storage.runs import RunRepository


@dataclass(frozen=True)
class PipelineSummary:
    run_id: str
    platform: str
    source: str
    received: int
    accepted: int
    quarantined: int
    reconciliation: ReconciliationResult | None = None


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
            valid_metrics = [result.metric for result in valid]

            # 对账：仅 Shopee 平台需要检查概览 vs 明细一致性
            reconciliation = None
            if collector.platform is Platform.SHOPEE and valid_metrics:
                reconciliation = MetricRepository.reconcile_ad_metrics(
                    valid_metrics,
                    threshold=DEFAULT_RECONCILIATION_THRESHOLD,
                )

            with self.database.transaction() as connection:
                MetricRepository.delete_platform_day(
                    connection,
                    collector.platform.value,
                    data_date.isoformat(),
                )

                extra_quarantined = 0
                if (
                    reconciliation
                    and reconciliation.status == "exceeded"
                ):
                    # 对账失败：只写概览行，明细行进入隔离
                    overview_metrics = [
                        m
                        for m in valid_metrics
                        if m.sku_id == OVERVIEW_SKU
                    ]
                    accepted = MetricRepository.upsert_many(
                        connection, overview_metrics
                    )
                    MetricRepository.quarantine_metrics(
                        connection,
                        run_id,
                        reconciliation.details_to_quarantine,
                        issues_json=(
                            '{"reconciliation":"exceeded",'
                            f'"overview_spend":"{reconciliation.overview_spend}",'
                            f'"detail_spend":"{reconciliation.detail_spend}"'
                            "}"
                        ),
                    )
                    extra_quarantined = len(
                        reconciliation.details_to_quarantine
                    )
                else:
                    accepted = MetricRepository.upsert_many(
                        connection, valid_metrics
                    )

                # 记录对账日志
                if reconciliation is not None:
                    MetricRepository.log_reconciliation(
                        connection,
                        platform=collector.platform.value,
                        store=(
                            valid_metrics[0].store
                            if valid_metrics
                            else ""
                        ),
                        account_id=(
                            valid_metrics[0].account_id
                            if valid_metrics
                            else ""
                        ),
                        data_date=data_date,
                        result=reconciliation,
                    )

                self.runs.store_quality(
                    connection, run_id, valid, invalid
                )

            total_quarantined = len(invalid) + extra_quarantined
            self.runs.finish(
                run_id,
                received=len(records),
                accepted=accepted,
                quarantined=total_quarantined,
            )
            return PipelineSummary(
                run_id=run_id,
                platform=collector.platform.value,
                source=collector.source,
                received=len(records),
                accepted=accepted,
                quarantined=total_quarantined,
                reconciliation=reconciliation,
            )
        except Exception as error:
            self.runs.fail(run_id, error)
            raise

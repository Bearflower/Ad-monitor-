from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from adwatch.analytics.refund_closure import RefundClosureService
from adwatch.analytics.service import AnalysisService
from adwatch.storage.db import Database


@dataclass(frozen=True)
class RecalculationDay:
    data_date: str
    metrics_processed: int
    profit_results: int
    pending_data: int


@dataclass(frozen=True)
class RecalculationResult:
    run_id: str
    start_date: str
    end_date: str
    days_processed: int
    metrics_processed: int
    profit_results: int
    pending_data: int
    missing_inputs: dict[str, int]
    differences: dict[str, dict[str, int]]
    dates: tuple[RecalculationDay, ...]
    report_path: Path


class AnalysisRecalculationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def run(
        self,
        *,
        start: date,
        end: date,
        destination: Path,
    ) -> RecalculationResult:
        if start > end:
            raise ValueError("start date must not be after end date")

        run_id = uuid.uuid4().hex
        started_at = datetime.now(UTC).isoformat()
        report_name = _safe_report_name(destination)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO analysis_recalculation_runs(
                    id, start_date, end_date, status, report_name, started_at
                ) VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (
                    run_id,
                    start.isoformat(),
                    end.isoformat(),
                    report_name,
                    started_at,
                ),
            )

        try:
            metric_days = self._metric_days(start, end)
            before_profit = self._snapshot_profit(metric_days)
            before_lineage = self._snapshot_lineage(metric_days)
            analysis = AnalysisService(self.database)
            with self.database.transaction() as connection:
                RefundClosureService(self.database).close(
                    start=start,
                    end=end,
                    _connection=connection,
                )
                day_results = tuple(
                    RecalculationDay(
                        data_date=data_date.isoformat(),
                        metrics_processed=summary.metrics_processed,
                        profit_results=summary.profit_results,
                        pending_data=summary.pending_data,
                    )
                    for data_date in metric_days
                    for summary in (
                        analysis.run_in_transaction(data_date, connection),
                    )
                )
                after_profit = self._snapshot_profit(
                    metric_days, connection=connection
                )
                after_lineage = self._snapshot_lineage(
                    metric_days, connection=connection
                )
                differences = {
                    "profit_results": _compare(before_profit, after_profit),
                    "input_lineage": _compare(before_lineage, after_lineage),
                }
                missing_inputs = self._missing_inputs(
                    metric_days, connection=connection
                )
                result = RecalculationResult(
                    run_id=run_id,
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    days_processed=len(day_results),
                    metrics_processed=sum(
                        item.metrics_processed for item in day_results
                    ),
                    profit_results=sum(
                        item.profit_results for item in day_results
                    ),
                    pending_data=sum(
                        item.pending_data for item in day_results
                    ),
                    missing_inputs=missing_inputs,
                    differences=differences,
                    dates=day_results,
                    report_path=destination,
                )
                self._write_report(result, started_at)
                connection.execute(
                    """
                    UPDATE analysis_recalculation_runs
                    SET status='succeeded', days_processed=?,
                        metrics_processed=?, profit_results=?, pending_data=?,
                        finished_at=?, error_code=NULL
                    WHERE id=?
                    """,
                    (
                        result.days_processed,
                        result.metrics_processed,
                        result.profit_results,
                        result.pending_data,
                        datetime.now(UTC).isoformat(),
                        run_id,
                    ),
                )
                return result
        except Exception:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE analysis_recalculation_runs
                    SET status='failed', finished_at=?,
                        error_code='analysis_failed'
                    WHERE id=?
                    """,
                    (datetime.now(UTC).isoformat(), run_id),
                )
            raise

    def _metric_days(self, start: date, end: date) -> tuple[date, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT data_date
                FROM daily_ad_metrics
                WHERE data_date BETWEEN ? AND ?
                ORDER BY data_date
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return tuple(date.fromisoformat(row[0]) for row in rows)

    def _snapshot_profit(
        self, metric_days: tuple[date, ...], *, connection=None
    ) -> dict[tuple[Any, ...], tuple[Any, ...]]:
        return self._snapshot(
            table="profit_results",
            metric_days=metric_days,
            key_columns=(
                "platform",
                "store",
                "account_id",
                "campaign_id",
                "sku_id",
                "data_date",
            ),
            value_columns=(
                "net_sales_cny",
                "platform_commission_cny",
                "gross_profit_cny",
                "net_profit_cny",
                "break_even_roas",
                "fee_effective_rate",
                "fee_period_start",
                "fee_period_end",
                "fee_selection",
                "fee_source_name",
                "fee_source_sha256",
            ),
            connection=connection,
        )

    def _snapshot_lineage(
        self, metric_days: tuple[date, ...], *, connection=None
    ) -> dict[tuple[Any, ...], tuple[Any, ...]]:
        return self._snapshot(
            table="profit_input_lineage",
            metric_days=metric_days,
            key_columns=(
                "platform",
                "store",
                "account_id",
                "campaign_id",
                "sku_id",
                "data_date",
                "input_name",
            ),
            value_columns=(
                "value_text",
                "status",
                "source_type",
                "source_date",
                "age_days",
                "source_ref",
                "detail_code",
            ),
            connection=connection,
        )

    def _snapshot(
        self,
        *,
        table: str,
        metric_days: tuple[date, ...],
        key_columns: tuple[str, ...],
        value_columns: tuple[str, ...],
        connection=None,
    ) -> dict[tuple[Any, ...], tuple[Any, ...]]:
        if not metric_days:
            return {}
        columns = (*key_columns, *value_columns)
        placeholders = ", ".join("?" for _ in metric_days)
        query = (
            f"SELECT {', '.join(columns)} FROM {table} "
            f"WHERE data_date IN ({placeholders})"
        )
        params = tuple(item.isoformat() for item in metric_days)
        if connection is None:
            with self.database.connect() as owned_connection:
                rows = owned_connection.execute(query, params).fetchall()
        else:
            rows = connection.execute(query, params).fetchall()
        key_size = len(key_columns)
        return {
            tuple(row[:key_size]): tuple(row[key_size:])
            for row in rows
        }

    def _missing_inputs(
        self, metric_days: tuple[date, ...], *, connection=None
    ) -> dict[str, int]:
        if not metric_days:
            return {}
        placeholders = ", ".join("?" for _ in metric_days)
        query = f"""
            SELECT input_name, detail_code, COUNT(*) AS missing_count
            FROM profit_input_lineage
            WHERE data_date IN ({placeholders})
              AND status IN ('pending_data', 'ambiguous')
            GROUP BY input_name, detail_code
            ORDER BY input_name, detail_code
        """
        params = tuple(item.isoformat() for item in metric_days)
        if connection is None:
            with self.database.connect() as owned_connection:
                rows = owned_connection.execute(query, params).fetchall()
        else:
            rows = connection.execute(query, params).fetchall()
        result: dict[str, int] = {}
        for row in rows:
            key = (
                "order_cost_coverage"
                if (
                    row["input_name"] == "product_cost"
                    and row["detail_code"] == "order_cost_coverage_incomplete"
                )
                else row["input_name"]
            )
            result[key] = result.get(key, 0) + row["missing_count"]
        return result

    @staticmethod
    def _write_report(result: RecalculationResult, started_at: str) -> None:
        destination = result.report_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{result.run_id}.tmp"
        )
        payload = {
            "run_id": result.run_id,
            "status": "succeeded",
            "start_date": result.start_date,
            "end_date": result.end_date,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "days_processed": result.days_processed,
            "metrics_processed": result.metrics_processed,
            "profit_results": result.profit_results,
            "pending_data": result.pending_data,
            "missing_inputs": result.missing_inputs,
            "differences": result.differences,
            "dates": [asdict(item) for item in result.dates],
        }
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def _compare(
    before: dict[tuple[Any, ...], tuple[Any, ...]],
    after: dict[tuple[Any, ...], tuple[Any, ...]],
) -> dict[str, int]:
    before_keys = set(before)
    after_keys = set(after)
    common = before_keys & after_keys
    return {
        "added": len(after_keys - before_keys),
        "changed": sum(before[key] != after[key] for key in common),
        "unchanged": sum(before[key] == after[key] for key in common),
        "removed": len(before_keys - after_keys),
    }


def _safe_report_name(destination: Path) -> str:
    value = "".join(
        char
        for char in destination.name
        if ord(char) >= 32 and ord(char) != 127
    )[:200]
    return value or "profit-recalculation.json"

import json
from datetime import date

import pytest

from adwatch.analytics.recalculation import AnalysisRecalculationService
from adwatch.analytics.service import AnalysisService
from adwatch.collectors.mock import MockCollector
from adwatch.domain import Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


def _complete_day(database: Database, data_date: date) -> None:
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    AnalysisService(database).seed_mock_business_data(data_date)


def test_recalculation_only_processes_metric_days_and_reports_stable_diff(
    tmp_path,
):
    database = Database(tmp_path / "recalculation.sqlite3")
    database.migrate()
    _complete_day(database, date(2026, 7, 20))
    _complete_day(database, date(2026, 7, 22))
    service = AnalysisRecalculationService(database)
    first_report = tmp_path / "reports" / "first.json"

    first = service.run(
        start=date(2026, 7, 19),
        end=date(2026, 7, 23),
        destination=first_report,
    )

    assert first.days_processed == 2
    assert first.metrics_processed == 8
    assert first.profit_results == 8
    assert first.pending_data == 0
    assert first.differences["profit_results"] == {
        "added": 8,
        "changed": 0,
        "unchanged": 0,
        "removed": 0,
    }
    assert first.differences["input_lineage"] == {
        "added": 72,
        "changed": 0,
        "unchanged": 0,
        "removed": 0,
    }
    payload = json.loads(first_report.read_text(encoding="utf-8"))
    assert payload["dates"] == [
        {
            "data_date": "2026-07-20",
            "metrics_processed": 4,
            "pending_data": 0,
            "profit_results": 4,
        },
        {
            "data_date": "2026-07-22",
            "metrics_processed": 4,
            "pending_data": 0,
            "profit_results": 4,
        },
    ]
    assert list(first_report.parent.iterdir()) == [first_report]

    second_report = tmp_path / "reports" / "second.json"
    second = service.run(
        start=date(2026, 7, 19),
        end=date(2026, 7, 23),
        destination=second_report,
    )

    assert second.differences["profit_results"] == {
        "added": 0,
        "changed": 0,
        "unchanged": 8,
        "removed": 0,
    }
    assert second.differences["input_lineage"] == {
        "added": 0,
        "changed": 0,
        "unchanged": 72,
        "removed": 0,
    }
    with database.connect() as connection:
        audit = connection.execute(
            """
            SELECT status, days_processed, metrics_processed, profit_results,
                   pending_data, report_name, finished_at, error_code
            FROM analysis_recalculation_runs ORDER BY started_at
            """
        ).fetchall()
    assert [row["status"] for row in audit] == ["succeeded", "succeeded"]
    assert tuple(audit[-1])[1:6] == (2, 8, 8, 0, "second.json")
    assert audit[-1]["finished_at"] is not None
    assert audit[-1]["error_code"] is None


def test_recalculation_counts_each_missing_profit_input(tmp_path):
    data_date = date(2026, 7, 20)
    database = Database(tmp_path / "pending.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)
    destination = tmp_path / "pending.json"

    result = AnalysisRecalculationService(database).run(
        start=data_date,
        end=data_date,
        destination=destination,
    )

    assert result.profit_results == 0
    assert result.pending_data == 4
    assert result.missing_inputs == {
        "allocated_fixed_cost": 4,
        "commission_rate": 4,
        "coupons": 4,
        "exchange_rate": 4,
        "product_cost": 4,
        "refunds": 4,
        "seller_shipping": 4,
    }


def test_recalculation_closes_t3_refund_and_records_lineage_source(tmp_path):
    data_date = date(2026, 7, 20)
    database = Database(tmp_path / "recalculation-refund.sqlite3")
    database.migrate()
    _complete_day(database, data_date)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_sync_runs(
                id, platform, store, started_at, finished_at, status,
                order_count, sku_count, rejected_count, error_message,
                start_date, end_date, refund_count, logistics_count,
                inventory_snapshot_count
            ) VALUES(
                'coverage-run', 'shopee', 'Shopee TH Store',
                '2026-07-23T09:00:00Z', '2026-07-23T09:01:00Z',
                'success', 1, 1, 0, NULL,
                '2026-07-20', '2026-07-23', 0, 0, 0
            )
            """
        )

    result = AnalysisRecalculationService(database).run(
        start=data_date,
        end=data_date,
        destination=tmp_path / "recalculation-refund.json",
    )

    assert result.profit_results == 4
    assert "refunds" not in result.missing_inputs
    with database.connect() as connection:
        refund = connection.execute(
            """
            SELECT status, source_type, source_ref
            FROM daily_refund_inputs
            WHERE platform='shopee' AND store='Shopee TH Store'
              AND data_date=?
            """,
            (data_date.isoformat(),),
        ).fetchone()
        lineage = connection.execute(
            """
            SELECT source_type, detail_code
            FROM profit_input_lineage
            WHERE data_date=? AND input_name='refunds'
            LIMIT 1
            """,
            (data_date.isoformat(),),
        ).fetchone()
    assert tuple(refund) == (
        "t3_closed_zero",
        "t3_no_refund_observed",
        "t3_no_refund_observed",
    )
    assert tuple(lineage) == (
        "daily_refund_inputs",
        "t3_no_refund_observed",
    )


def test_recalculation_rejects_reversed_range_before_creating_audit(tmp_path):
    database = Database(tmp_path / "reversed.sqlite3")
    database.migrate()

    with pytest.raises(ValueError, match="start date must not be after end date"):
        AnalysisRecalculationService(database).run(
            start=date(2026, 7, 22),
            end=date(2026, 7, 20),
            destination=tmp_path / "invalid.json",
        )

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM analysis_recalculation_runs"
        ).fetchone()[0]
    assert count == 0


def test_recalculation_records_safe_failure_without_exception_text(
    tmp_path, monkeypatch
):
    data_date = date(2026, 7, 20)
    database = Database(tmp_path / "failed.sqlite3")
    database.migrate()
    PipelineRunner(database).run(MockCollector(Platform.SHOPEE), data_date)

    def fail_analysis(self, day, connection):
        raise RuntimeError("secret local path /Users/example/private")

    monkeypatch.setattr(AnalysisService, "run_in_transaction", fail_analysis)
    with pytest.raises(RuntimeError, match="secret local path"):
        AnalysisRecalculationService(database).run(
            start=data_date,
            end=data_date,
            destination=tmp_path / "failed.json",
        )

    with database.connect() as connection:
        audit = connection.execute(
            """
            SELECT status, report_name, error_code, finished_at
            FROM analysis_recalculation_runs
            """
        ).fetchone()
    assert tuple(audit)[:3] == (
        "failed",
        "failed.json",
        "analysis_failed",
    )
    assert audit["finished_at"] is not None
    assert "/Users/" not in " ".join(str(value) for value in audit)


def test_recalculation_rolls_back_all_days_when_later_day_fails(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "atomic-failure.sqlite3")
    database.migrate()
    first_day = date(2026, 7, 20)
    second_day = date(2026, 7, 21)
    _complete_day(database, first_day)
    _complete_day(database, second_day)
    original = AnalysisService.run_in_transaction

    def fail_second_day(self, data_date, connection):
        if data_date == second_day:
            raise RuntimeError("second day failed")
        return original(self, data_date, connection)

    monkeypatch.setattr(
        AnalysisService, "run_in_transaction", fail_second_day
    )
    destination = tmp_path / "atomic-failure.json"

    with pytest.raises(RuntimeError, match="second day failed"):
        AnalysisRecalculationService(database).run(
            start=first_day,
            end=second_day,
            destination=destination,
        )

    with database.connect() as connection:
        derived_counts = (
            connection.execute("SELECT COUNT(*) FROM profit_results").fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM profit_input_lineage"
            ).fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0],
        )
        audit = connection.execute(
            """
            SELECT status, days_processed, metrics_processed,
                   profit_results, pending_data, error_code
            FROM analysis_recalculation_runs
            """
        ).fetchone()

    assert derived_counts == (0, 0, 0)
    assert tuple(audit) == ("failed", 0, 0, 0, 0, "analysis_failed")
    assert not destination.exists()

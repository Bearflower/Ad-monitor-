from datetime import date

from adwatch.analytics.refund_closure import RefundClosureService
from adwatch.storage.db import Database


def _database(tmp_path):
    database = Database(tmp_path / "refund-closure.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id,
                data_date, currency, spend, attributed_gmv, orders,
                roas, cpa, source
            ) VALUES (
                'shopee', 'shop', 'account', 'campaign', '__ALL__',
                '2026-08-01', 'THB', '10', '100', 1,
                '10', '10', 'test'
            )
            """
        )
    return database


def _sync_run(database, *, status="success", start="2026-08-01", end="2026-08-04"):
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_sync_runs(
                id, platform, store, started_at, finished_at, status,
                order_count, sku_count, rejected_count, error_message,
                start_date, end_date, refund_count, logistics_count,
                inventory_snapshot_count
            ) VALUES (
                ?, 'shopee', 'shop', '2026-08-04T09:00:00Z',
                '2026-08-04T09:01:00Z', ?, 1, 1, 0, NULL, ?, ?, 0, 0, 0
            )
            """,
            (f"run-{status}", status, start, end),
        )


def _refund(
    database,
    *,
    amount=None,
    status="returned",
    currency="THB",
    observed_on="2026-08-04",
    observed_at="2026-08-04T09:00:00Z",
):
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_refund_observations(
                platform, store, order_id, observed_on, order_date,
                refund_status, amount_original, currency, source_updated_at,
                observed_at, source
            ) VALUES (
                'shopee', 'shop', 'ORDER-1', ?, '2026-08-01',
                ?, ?, ?, '2026-08-04T08:00:00Z',
                ?, 'test'
            )
            """,
            (observed_on, status, amount, currency, observed_at),
        )


def _daily_refund(database):
    with database.connect() as connection:
        return connection.execute(
            "SELECT * FROM daily_refund_inputs"
        ).fetchone()


def test_closes_zero_after_t3_when_successful_sync_covers_window(tmp_path):
    database = _database(tmp_path)
    _sync_run(database)

    result = RefundClosureService(
        database, today=date(2026, 8, 5)
    ).close(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.closed_zero == 1
    row = _daily_refund(database)
    assert row["status"] == "t3_closed_zero"
    assert row["refund_amount_original"] == "0"
    assert row["source_type"] == "t3_no_refund_observed"


def test_does_not_close_before_t3_or_after_partial_sync(tmp_path):
    database = _database(tmp_path)
    _sync_run(database, status="partial")

    result = RefundClosureService(
        database, today=date(2026, 8, 2)
    ).close(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.closed_zero == 0
    assert result.pending == 1
    assert _daily_refund(database)["source_ref"] == "t3_not_reached"


def test_refund_status_without_amount_stays_pending(tmp_path):
    database = _database(tmp_path)
    _sync_run(database)
    _refund(database)

    result = RefundClosureService(
        database, today=date(2026, 8, 5)
    ).close(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.pending == 1
    row = _daily_refund(database)
    assert row["status"] == "pending_data"
    assert row["source_ref"] == "refund_amount_missing"


def test_real_metric_without_sync_history_stays_pending(tmp_path):
    database = _database(tmp_path)

    result = RefundClosureService(
        database, today=date(2026, 8, 5)
    ).close(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.pending == 1
    assert _daily_refund(database)["source_ref"] == "sync_coverage_missing"


def test_latest_refund_observation_is_counted_once(tmp_path):
    database = _database(tmp_path)
    _sync_run(database)
    _refund(
        database,
        amount="10",
        observed_on="2026-08-02",
        observed_at="2026-08-02T09:00:00Z",
    )
    _refund(
        database,
        amount="12",
        observed_on="2026-08-04",
        observed_at="2026-08-04T09:00:00Z",
    )

    RefundClosureService(database, today=date(2026, 8, 5)).close(
        start=date(2026, 8, 1), end=date(2026, 8, 1)
    )

    assert _daily_refund(database)["refund_amount_original"] == "12"


def test_refund_currency_mismatch_stays_pending(tmp_path):
    database = _database(tmp_path)
    _sync_run(database)
    _refund(database, amount="12", currency="USD")

    result = RefundClosureService(
        database, today=date(2026, 8, 5)
    ).close(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.pending == 1
    assert _daily_refund(database)["source_ref"] == "refund_currency_mismatch"


def test_closure_is_idempotent(tmp_path):
    database = _database(tmp_path)
    _sync_run(database)
    service = RefundClosureService(database, today=date(2026, 8, 5))

    first = service.close(start=date(2026, 8, 1), end=date(2026, 8, 1))
    second = service.close(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert (first.closed_zero, second.closed_zero) == (1, 0)
    assert second.unchanged == 1


def test_existing_observed_amount_is_preserved(tmp_path):
    database = _database(tmp_path)
    _sync_run(database)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_refund_inputs(
                platform, store, data_date, refund_amount_original,
                currency, status, source_type, source_ref,
                observed_on, closed_at, updated_at
            ) VALUES (
                'shopee', 'shop', '2026-08-01', '12.50', 'THB',
                'observed', 'manual', 'import-1', '2026-08-04', NULL,
                '2026-08-04T09:00:00Z'
            )
            """
        )

    result = RefundClosureService(
        database, today=date(2026, 8, 5)
    ).close(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.unchanged == 1
    assert _daily_refund(database)["refund_amount_original"] == "12.50"

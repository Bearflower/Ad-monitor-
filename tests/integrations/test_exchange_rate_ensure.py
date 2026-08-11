from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from adwatch.integrations.exchange_rates import (
    ensure_exchange_rate,
    resolve_scheduled_exchange_rate,
)
from adwatch.storage.db import Database


class OfflineSource:
    def fetch_range(self, currency, start, end):
        raise OSError("offline")


@dataclass(frozen=True)
class PublishedSource:
    values: dict[date, Decimal]

    def fetch_published(self, currency, start, end):
        return {
            day: value
            for day, value in self.values.items()
            if start <= day <= end
        }


def _database(tmp_path) -> Database:
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    return database


def _database_with_rate(tmp_path, rate_date: str) -> Database:
    database = _database(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
            VALUES ('THB', ?, '0.201493044661')
            """,
            (rate_date,),
        )
    return database


def test_ensure_exchange_rate_copies_recent_local_rate_when_remote_fails(
    tmp_path,
):
    database = _database_with_rate(tmp_path, "2026-07-27")

    result = ensure_exchange_rate(
        database,
        OfflineSource(),
        currency="THB",
        data_date=date(2026, 7, 28),
    )

    assert result.status == "local_fallback"
    assert result.source_date == date(2026, 7, 27)
    assert result.rate == Decimal("0.201493044661")
    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT rate_to_cny FROM exchange_rates
            WHERE currency='THB' AND rate_date='2026-07-28'
            """
        ).fetchone()
    assert stored[0] == "0.201493044661"


def test_ensure_exchange_rate_rejects_local_rate_older_than_seven_days(
    tmp_path,
):
    database = _database_with_rate(tmp_path, "2026-07-20")

    with pytest.raises(ValueError, match="older than 7 days"):
        ensure_exchange_rate(
            database,
            OfflineSource(),
            currency="THB",
            data_date=date(2026, 7, 28),
        )


def test_scheduled_rate_uses_execution_day_and_applies_to_report_day(
    tmp_path,
):
    database = _database(tmp_path)

    result = resolve_scheduled_exchange_rate(
        database,
        PublishedSource({date(2026, 7, 30): Decimal("0.201")}),
        currency="THB",
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        max_staleness_days=7,
        resolved_at=datetime(
            2026,
            7,
            30,
            9,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
    )

    assert result.data_date == date(2026, 7, 29)
    assert result.source_date == date(2026, 7, 30)
    assert result.execution_date == date(2026, 7, 30)
    assert result.status == "execution_day"
    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT execution_date, source_date, resolution_status, resolved_at
            FROM exchange_rates
            WHERE currency='THB' AND rate_date='2026-07-29'
            """
        ).fetchone()
    assert tuple(stored) == (
        "2026-07-30",
        "2026-07-30",
        "execution_day",
        "2026-07-30T09:00:00+08:00",
    )


def test_scheduled_rate_uses_previous_day_when_execution_day_missing(
    tmp_path,
):
    result = resolve_scheduled_exchange_rate(
        _database(tmp_path),
        PublishedSource({date(2026, 7, 29): Decimal("0.202")}),
        currency="THB",
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        max_staleness_days=7,
    )

    assert result.source_date == date(2026, 7, 29)
    assert result.status == "previous_day"


def test_scheduled_rate_uses_recent_business_day_within_window(tmp_path):
    result = resolve_scheduled_exchange_rate(
        _database(tmp_path),
        PublishedSource({date(2026, 7, 27): Decimal("0.203")}),
        currency="THB",
        execution_date=date(2026, 7, 30),
        report_date=date(2026, 7, 29),
        max_staleness_days=7,
    )

    assert result.source_date == date(2026, 7, 27)
    assert result.status == "recent_business_day"


def test_scheduled_rate_rejects_source_older_than_window(tmp_path):
    with pytest.raises(
        ValueError,
        match="no THB/CNY exchange rate within configured window",
    ):
        resolve_scheduled_exchange_rate(
            _database(tmp_path),
            PublishedSource({date(2026, 7, 22): Decimal("0.204")}),
            currency="THB",
            execution_date=date(2026, 7, 30),
            report_date=date(2026, 7, 29),
            max_staleness_days=7,
        )

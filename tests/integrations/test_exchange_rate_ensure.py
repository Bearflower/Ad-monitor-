from datetime import date
from decimal import Decimal

import pytest

from adwatch.integrations.exchange_rates import ensure_exchange_rate
from adwatch.storage.db import Database


class OfflineSource:
    def fetch_range(self, currency, start, end):
        raise OSError("offline")


def _database_with_rate(tmp_path, rate_date: str) -> Database:
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
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

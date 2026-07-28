from datetime import date
from decimal import Decimal

from adwatch.integrations.exchange_rates import sync_exchange_rates
from adwatch.storage.db import Database


class StubSource:
    def fetch_range(self, currency, start, end):
        assert (currency, start, end) == (
            "THB",
            date(2026, 7, 26),
            date(2026, 7, 27),
        )
        return {
            date(2026, 7, 26): Decimal("0.201"),
            date(2026, 7, 27): Decimal("0.202"),
        }


def test_sync_exchange_rates_upserts_requested_dates(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    count = sync_exchange_rates(
        database,
        StubSource(),
        currency="THB",
        start=date(2026, 7, 26),
        end=date(2026, 7, 27),
    )

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT currency, rate_date, rate_to_cny
            FROM exchange_rates ORDER BY rate_date
            """
        ).fetchall()
    assert count == 2
    assert [tuple(row) for row in rows] == [
        ("THB", "2026-07-26", "0.201"),
        ("THB", "2026-07-27", "0.202"),
    ]

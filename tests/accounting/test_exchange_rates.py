from datetime import date
from decimal import Decimal

import pytest

from adwatch.accounting.exchange_rates import (
    AccountingExchangeRateService,
    ExchangeRateInputError,
)
from adwatch.storage.db import Database


def _database(tmp_path):
    database = Database(tmp_path / "exchange-rates.sqlite3")
    database.migrate()
    return database


def test_monthly_override_precedes_exact_and_prior_daily_rate(tmp_path):
    database = _database(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO exchange_rates(currency, rate_date, rate_to_cny) "
            "VALUES ('THB','2026-08-01','0.20')"
        )
        connection.execute(
            "INSERT INTO exchange_rates(currency, rate_date, rate_to_cny) "
            "VALUES ('THB','2026-08-05','0.21')"
        )
    service = AccountingExchangeRateService(database, max_staleness_days=7)
    assert service.resolve("THB", date(2026, 8, 5)).rate == Decimal("0.21")
    service.set_monthly_override(
        currency="THB",
        year_month="2026-08",
        rate_to_cny=Decimal("0.22"),
        source="manual",
        note="月结汇率",
        actor="yl",
    )
    resolution = service.resolve("฿", date(2026, 8, 5))
    assert (
        resolution.rate,
        resolution.source,
        resolution.source_date,
    ) == (Decimal("0.22"), "monthly_override", date(2026, 8, 1))


def test_cny_is_one_and_missing_or_stale_rate_is_not_zero(tmp_path):
    database = _database(tmp_path)
    service = AccountingExchangeRateService(database, max_staleness_days=7)
    assert service.resolve("¥", date(2026, 8, 5)).rate == Decimal(1)
    with pytest.raises(ExchangeRateInputError, match="no THB/CNY"):
        service.resolve("THB", date(2026, 8, 5))
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO exchange_rates(currency, rate_date, rate_to_cny) "
            "VALUES ('THB','2026-07-01','0.20')"
        )
    with pytest.raises(ExchangeRateInputError, match="older than 7 days"):
        service.resolve("THB", date(2026, 8, 5))


def test_monthly_override_validates_month_rate_and_actor(tmp_path):
    service = AccountingExchangeRateService(
        _database(tmp_path), max_staleness_days=7
    )
    for kwargs, message in (
        ({"year_month": "2026/08"}, "YYYY-MM"),
        ({"rate_to_cny": Decimal(0)}, "positive"),
        ({"actor": ""}, "actor"),
    ):
        values = {
            "currency": "THB",
            "year_month": "2026-08",
            "rate_to_cny": Decimal("0.20"),
            "source": "manual",
            "note": "",
            "actor": "yl",
            **kwargs,
        }
        with pytest.raises(ExchangeRateInputError, match=message):
            service.set_monthly_override(**values)

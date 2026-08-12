from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.accounting.money import normalize_currency
from adwatch.storage.db import Database


class ExchangeRateInputError(ValueError):
    pass


@dataclass(frozen=True)
class AccountingRateResolution:
    currency: str
    data_date: date
    rate: Decimal
    source: str
    source_date: date


class AccountingExchangeRateService:
    def __init__(
        self,
        database: Database,
        max_staleness_days: int = 7,
    ) -> None:
        self.database = database
        self.max_staleness_days = max_staleness_days

    def set_monthly_override(
        self,
        *,
        currency: str,
        year_month: str,
        rate_to_cny: Decimal,
        source: str,
        note: str,
        actor: str,
    ) -> None:
        normalized = normalize_currency(currency)
        if not _valid_year_month(year_month):
            raise ExchangeRateInputError("year_month must use YYYY-MM")
        if rate_to_cny <= 0:
            raise ExchangeRateInputError("rate must be positive")
        source = source.strip()
        actor = actor.strip()
        if not source:
            raise ExchangeRateInputError("source is required")
        if not actor:
            raise ExchangeRateInputError("actor is required")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO monthly_exchange_rate_overrides(
                    currency, year_month, rate_to_cny, source, note,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(currency, year_month) DO UPDATE SET
                    rate_to_cny=excluded.rate_to_cny,
                    source=excluded.source,
                    note=excluded.note,
                    created_by=excluded.created_by,
                    created_at=excluded.created_at
                """,
                (
                    normalized,
                    year_month,
                    str(rate_to_cny),
                    source,
                    note,
                    actor,
                    now,
                ),
            )

    def resolve(
        self,
        currency: str,
        data_date: date,
    ) -> AccountingRateResolution:
        normalized = normalize_currency(currency)
        if normalized == "CNY":
            return AccountingRateResolution(
                normalized,
                data_date,
                Decimal(1),
                "identity",
                data_date,
            )
        year_month = data_date.strftime("%Y-%m")
        with self.database.connect() as connection:
            monthly = connection.execute(
                """
                SELECT rate_to_cny
                FROM monthly_exchange_rate_overrides
                WHERE currency=? AND year_month=?
                """,
                (normalized, year_month),
            ).fetchone()
            if monthly is not None:
                return AccountingRateResolution(
                    normalized,
                    data_date,
                    Decimal(monthly["rate_to_cny"]),
                    "monthly_override",
                    date.fromisoformat(f"{year_month}-01"),
                )
            row = connection.execute(
                """
                SELECT rate_date, rate_to_cny
                FROM exchange_rates
                WHERE currency=? AND rate_date<=?
                ORDER BY rate_date DESC
                LIMIT 1
                """,
                (normalized, data_date.isoformat()),
            ).fetchone()
        if row is None:
            raise ExchangeRateInputError(
                f"no {normalized}/CNY exchange rate on or before {data_date}"
            )
        source_date = date.fromisoformat(row["rate_date"])
        age = (data_date - source_date).days
        if age > self.max_staleness_days:
            raise ExchangeRateInputError(
                f"latest {normalized}/CNY exchange rate is older than "
                f"{self.max_staleness_days} days"
            )
        source = "exact_daily" if source_date == data_date else "prior_daily"
        return AccountingRateResolution(
            normalized,
            data_date,
            Decimal(row["rate_to_cny"]),
            source,
            source_date,
        )

    def list_monthly_rates(self, year: int) -> tuple[dict, ...]:
        prefix = f"{year:04d}-%"
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT currency, year_month, rate_to_cny, source, note,
                       created_by, created_at
                FROM monthly_exchange_rate_overrides
                WHERE year_month LIKE ?
                ORDER BY year_month, currency
                """,
                (prefix,),
            ).fetchall()
        return tuple(dict(row) for row in rows)


def _valid_year_month(value: str) -> bool:
    if len(value) != 7 or value[4] != "-":
        return False
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError:
        return False
    return True

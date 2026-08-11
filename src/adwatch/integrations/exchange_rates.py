from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from urllib.request import urlopen
from xml.etree import ElementTree

from adwatch.storage.db import Database

ECB_HISTORY_URL = (
    "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
)


class ExchangeRateRangeSource(Protocol):
    def fetch_range(
        self, currency: str, start: date, end: date
    ) -> dict[date, Decimal]: ...


class PublishedExchangeRateSource(Protocol):
    def fetch_published(
        self, currency: str, start: date, end: date
    ) -> dict[date, Decimal]: ...


@dataclass(frozen=True)
class ExchangeRateResolution:
    currency: str
    data_date: date
    rate: Decimal
    status: str
    source_date: date
    execution_date: date | None = None


@dataclass(frozen=True)
class StaticExchangeRateSource:
    values: dict[tuple[str, date], Decimal]

    def fetch(self, currency: str, data_date: date) -> Decimal | None:
        return self.values.get((currency, data_date))


def _download(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


@dataclass(frozen=True)
class EcbExchangeRateSource:
    """Convert ECB euro reference rates into one source unit in CNY."""

    fetcher: Callable[[str], bytes] = _download
    url: str = ECB_HISTORY_URL

    def fetch_published(
        self, currency: str, start: date, end: date
    ) -> dict[date, Decimal]:
        if start > end:
            raise ValueError("exchange-rate start date must not exceed end date")
        currency = currency.strip().upper()
        if currency == "CNY":
            return {
                start + timedelta(days=offset): Decimal(1)
                for offset in range((end - start).days + 1)
            }

        root = ElementTree.fromstring(self.fetcher(self.url))
        published: dict[date, Decimal] = {}
        for cube in root.iter():
            time_value = cube.attrib.get("time")
            if not time_value:
                continue
            rates = {
                child.attrib.get("currency"): Decimal(child.attrib["rate"])
                for child in cube
                if child.attrib.get("currency") and child.attrib.get("rate")
            }
            if currency in rates and "CNY" in rates:
                published_day = date.fromisoformat(time_value)
                if start <= published_day <= end:
                    published[published_day] = (
                        rates["CNY"] / rates[currency]
                    )
        return published

    def fetch_range(
        self, currency: str, start: date, end: date
    ) -> dict[date, Decimal]:
        if start > end:
            raise ValueError("exchange-rate start date must not exceed end date")
        if currency.strip().upper() == "CNY":
            return self.fetch_published(currency, start, end)

        published = self.fetch_published(currency, date.min, end)
        result: dict[date, Decimal] = {}
        applicable = sorted(day for day in published if day <= end)
        current = start
        while current <= end:
            source_days = [day for day in applicable if day <= current]
            if not source_days:
                raise ValueError(
                    f"ECB has no {currency}/CNY rate on or before {current}"
                )
            result[current] = published[source_days[-1]]
            current += timedelta(days=1)
        return result


def sync_exchange_rates(
    database: Database,
    source: ExchangeRateRangeSource,
    *,
    currency: str,
    start: date,
    end: date,
) -> int:
    rates = source.fetch_range(currency, start, end)
    with database.transaction() as connection:
        for rate_date, rate in sorted(rates.items()):
            connection.execute(
                """
                INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
                VALUES (?, ?, ?)
                ON CONFLICT(currency, rate_date) DO UPDATE SET
                    rate_to_cny=excluded.rate_to_cny
                """,
                (currency.upper(), rate_date.isoformat(), str(rate)),
            )
    return len(rates)


def ensure_exchange_rate(
    database: Database,
    source: ExchangeRateRangeSource,
    *,
    currency: str,
    data_date: date,
    max_staleness_days: int = 7,
) -> ExchangeRateResolution:
    currency = currency.strip().upper()
    try:
        rates = source.fetch_range(currency, data_date, data_date)
    except (OSError, ValueError):
        rates = {}
    if data_date in rates:
        rate = rates[data_date]
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
                VALUES (?, ?, ?)
                ON CONFLICT(currency, rate_date) DO UPDATE SET
                    rate_to_cny=excluded.rate_to_cny
                """,
                (currency, data_date.isoformat(), str(rate)),
            )
        return ExchangeRateResolution(
            currency, data_date, rate, "fresh", data_date
        )

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT rate_date, rate_to_cny
            FROM exchange_rates
            WHERE currency=? AND rate_date<=?
            ORDER BY rate_date DESC
            LIMIT 1
            """,
            (currency, data_date.isoformat()),
        ).fetchone()
    if row is None:
        raise ValueError(
            f"no {currency}/CNY exchange rate on or before {data_date}"
        )
    source_date = date.fromisoformat(row["rate_date"])
    age = (data_date - source_date).days
    if age > max_staleness_days:
        raise ValueError(
            f"latest {currency}/CNY exchange rate is older than "
            f"{max_staleness_days} days"
        )
    rate = Decimal(row["rate_to_cny"])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
            VALUES (?, ?, ?)
            ON CONFLICT(currency, rate_date) DO UPDATE SET
                rate_to_cny=excluded.rate_to_cny
            """,
            (currency, data_date.isoformat(), str(rate)),
        )
    return ExchangeRateResolution(
        currency, data_date, rate, "local_fallback", source_date
    )


def resolve_scheduled_exchange_rate(
    database: Database,
    source: PublishedExchangeRateSource,
    *,
    currency: str,
    execution_date: date,
    report_date: date,
    max_staleness_days: int,
    resolved_at: datetime | None = None,
) -> ExchangeRateResolution:
    currency = currency.strip().upper()
    start = execution_date - timedelta(days=max_staleness_days)
    resolution_time = resolved_at or datetime.now().astimezone()
    try:
        published = source.fetch_published(currency, start, execution_date)
    except (OSError, ValueError):
        published = {}

    candidates = [
        day for day in published if start <= day <= execution_date
    ]
    if candidates:
        source_date = max(candidates)
        return _store_scheduled_resolution(
            database=database,
            currency=currency,
            report_date=report_date,
            execution_date=execution_date,
            source_date=source_date,
            rate=published[source_date],
            status=_scheduled_resolution_status(
                execution_date, source_date
            ),
            resolved_at=resolution_time,
        )

    local = _latest_local_rate(
        database,
        currency=currency,
        start=start,
        end=execution_date,
    )
    if local is None:
        raise ValueError(
            f"no {currency}/CNY exchange rate within configured window"
        )
    source_date, rate = local
    return _store_scheduled_resolution(
        database=database,
        currency=currency,
        report_date=report_date,
        execution_date=execution_date,
        source_date=source_date,
        rate=rate,
        status=_scheduled_resolution_status(execution_date, source_date),
        resolved_at=resolution_time,
    )


def _scheduled_resolution_status(
    execution_date: date, source_date: date
) -> str:
    if source_date == execution_date:
        return "execution_day"
    if source_date == execution_date - timedelta(days=1):
        return "previous_day"
    return "recent_business_day"


def _latest_local_rate(
    database: Database,
    *,
    currency: str,
    start: date,
    end: date,
) -> tuple[date, Decimal] | None:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(source_date, rate_date) AS actual_source_date,
                   rate_to_cny
            FROM exchange_rates
            WHERE currency=?
              AND COALESCE(source_date, rate_date) BETWEEN ? AND ?
            ORDER BY actual_source_date DESC
            LIMIT 1
            """,
            (currency, start.isoformat(), end.isoformat()),
        ).fetchone()
    if row is None:
        return None
    return (
        date.fromisoformat(row["actual_source_date"]),
        Decimal(row["rate_to_cny"]),
    )


def _store_scheduled_resolution(
    *,
    database: Database,
    currency: str,
    report_date: date,
    execution_date: date,
    source_date: date,
    rate: Decimal,
    status: str,
    resolved_at: datetime,
) -> ExchangeRateResolution:
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO exchange_rates(
                currency, rate_date, rate_to_cny, execution_date,
                source_date, resolution_status, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(currency, rate_date) DO UPDATE SET
                rate_to_cny=excluded.rate_to_cny,
                execution_date=excluded.execution_date,
                source_date=excluded.source_date,
                resolution_status=excluded.resolution_status,
                resolved_at=excluded.resolved_at
            """,
            (
                currency,
                report_date.isoformat(),
                str(rate),
                execution_date.isoformat(),
                source_date.isoformat(),
                status,
                resolved_at.isoformat(),
            ),
        )
    return ExchangeRateResolution(
        currency=currency,
        data_date=report_date,
        rate=rate,
        status=status,
        source_date=source_date,
        execution_date=execution_date,
    )

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol
from urllib.request import urlopen
from xml.etree import ElementTree

from adwatch.storage.db import Database

ECB_HISTORY_URL = (
    "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
)


class ExchangeRateRangeSource(Protocol):
    def fetch_range(
        self, currency: str, start: date, end: date
    ) -> dict[date, Decimal]: ...


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

    def fetch_range(
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
                published[date.fromisoformat(time_value)] = (
                    rates["CNY"] / rates[currency]
                )

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

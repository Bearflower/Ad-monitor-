from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class StaticExchangeRateSource:
    values: dict[tuple[str, date], Decimal]

    def fetch(self, currency: str, data_date: date) -> Decimal | None:
        return self.values.get((currency, data_date))

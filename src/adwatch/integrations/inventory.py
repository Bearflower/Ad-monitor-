from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class StaticInventorySource:
    values: dict[tuple[str, date], tuple[int, Decimal]]

    def fetch(
        self, sku_id: str, data_date: date
    ) -> tuple[int, Decimal] | None:
        return self.values.get((sku_id, data_date))

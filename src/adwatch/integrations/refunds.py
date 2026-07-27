from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class StaticRefundSource:
    values: dict[tuple[str, str, date], Decimal]

    def fetch(
        self,
        *,
        platform: str,
        campaign_id: str,
        data_date: date,
        as_of: date,
    ) -> Decimal | None:
        if as_of < data_date:
            return None
        return self.values.get((platform, campaign_id, data_date))

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class DailyPoint:
    data_date: date
    spend: Decimal
    gmv: Decimal


@dataclass(frozen=True)
class WindowSummary:
    spend: Decimal
    gmv: Decimal
    roas: Decimal | None


def summarize_window(
    points: list[DailyPoint], *, end: date, days: int
) -> WindowSummary:
    start = end - timedelta(days=days - 1)
    selected = [point for point in points if start <= point.data_date <= end]
    spend = sum((point.spend for point in selected), Decimal(0))
    gmv = sum((point.gmv for point in selected), Decimal(0))
    roas = None
    if spend != 0:
        roas = (gmv / spend).quantize(Decimal("0.0001"))
    return WindowSummary(
        spend=spend.quantize(Decimal("0.01")),
        gmv=gmv.quantize(Decimal("0.01")),
        roas=roas,
    )

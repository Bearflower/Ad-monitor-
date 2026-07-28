from datetime import date, timedelta
from decimal import Decimal

from adwatch.analytics.windows import DailyPoint, summarize_window


def test_window_uses_weighted_roas():
    end = date(2026, 7, 22)
    points = [
        DailyPoint(end - timedelta(days=1), Decimal(100), Decimal(200)),
        DailyPoint(end, Decimal(300), Decimal(300)),
    ]
    result = summarize_window(points, end=end, days=7)
    assert result.spend == Decimal("400.00")
    assert result.gmv == Decimal("500.00")
    assert result.roas == Decimal("1.2500")

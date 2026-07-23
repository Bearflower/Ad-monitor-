from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric, Platform


def test_metric_calculates_roas_and_cpa():
    metric = DailyAdMetric(
        platform=Platform.TIKTOK,
        store="MY Store",
        account_id="acct-1",
        campaign_id="camp-1",
        sku_id="SKU-1",
        data_date=date(2026, 7, 22),
        currency="MYR",
        spend=Decimal("100.00"),
        attributed_gmv=Decimal("350.00"),
        orders=7,
        source="mock",
    )
    assert metric.roas == Decimal("3.5000")
    assert metric.cpa == Decimal("14.2857")
    assert metric.logical_key == (
        "tiktok",
        "MY Store",
        "acct-1",
        "camp-1",
        "SKU-1",
        "2026-07-22",
    )

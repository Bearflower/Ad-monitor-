from dataclasses import replace
from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric, Platform
from adwatch.pipeline.validation import validate_metric

BASE = DailyAdMetric(
    platform=Platform.SHOPEE,
    store="TH Store",
    account_id="acct",
    campaign_id="camp",
    sku_id="SKU",
    data_date=date(2026, 7, 22),
    currency="THB",
    spend=Decimal(10),
    attributed_gmv=Decimal(20),
    orders=1,
    source="mock",
)


def test_negative_spend_is_quarantined():
    result = validate_metric(replace(BASE, spend=Decimal(-1)))
    assert result.is_valid is False
    assert {issue.code for issue in result.issues} == {"negative_spend"}


def test_unknown_currency_is_quarantined():
    result = validate_metric(replace(BASE, currency="XYZ"))
    assert result.is_valid is False
    assert {issue.code for issue in result.issues} == {"unknown_currency"}

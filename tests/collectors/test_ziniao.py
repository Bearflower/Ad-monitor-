from datetime import date
from decimal import Decimal

import pytest

from adwatch.collectors.ziniao import (
    ZiniaoCollector,
    ZiniaoNotConfigured,
    parse_shopee_campaign_summary,
    parse_shopee_product_rows,
)
from adwatch.config import Settings
from adwatch.domain import Platform


def test_ziniao_collector_fails_explicitly_when_unconfigured(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(ZiniaoNotConfigured):
        ZiniaoCollector(settings, Platform.SHOPEE).collect(date(2026, 7, 22))


def test_parse_shopee_product_rows_maps_currency_and_primary_values():
    rows = [
        {
            "campaign": "Shop GMV Max",
            "product": "Nike Body Spray ID: 54562829508 New Products",
            "metrics": [
                "15.3k +1,543.4%",
                "325 +1,150.0%",
                "2.12% -23.9%",
                "฿1,262.02 +2,463.6%",
                "฿4,002.00 +503.6%",
                "16 +1,500.0%",
                "19 +533.3%",
                "3.17 -76.5%",
            ],
        }
    ]

    metrics = parse_shopee_product_rows(
        rows,
        store="虾皮泰国",
        account_id="27679338786521",
        data_date=date(2026, 7, 22),
    )

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.campaign_id == "Shop GMV Max"
    assert metric.sku_id == "54562829508"
    assert metric.spend == Decimal("1262.02")
    assert metric.attributed_gmv == Decimal("4002.00")
    assert metric.orders == 16
    assert metric.currency == "THB"
    assert metric.source == "ziniao-cli"


def test_parse_shopee_product_rows_skips_summary_without_product_id():
    rows = [
        {
            "campaign": "Shop GMV Max",
            "product": "Shop GMV Max No end date",
            "metrics": ["101.6k", "1.9k", "1.90%", "฿4,673.78", "฿13,251", "74"],
        }
    ]

    assert (
        parse_shopee_product_rows(
            rows,
            store="虾皮泰国",
            account_id="27679338786521",
            data_date=date(2026, 7, 22),
        )
        == []
    )


def test_parse_shopee_campaign_summary_creates_single_all_products_metric():
    metric = parse_shopee_campaign_summary(
        {
            "campaign": "Shop GMV Max",
            "metrics": [
                "101.6k",
                "1.9k",
                "1.90%",
                "฿36.33",
                "฿231.00",
                "2",
            ],
        },
        store="虾皮泰国",
        account_id="27679338786521",
        data_date=date(2026, 7, 23),
    )

    assert metric.sku_id == "__ALL__"
    assert metric.spend == Decimal("36.33")
    assert metric.attributed_gmv == Decimal("231.00")
    assert metric.orders == 2


class FakeCliClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def navigate_and_exec(
        self,
        store_id,
        url,
        script,
        *,
        expected_url,
        require_nonempty=False,
        attempts=15,
    ):
        self.calls.append(
            (
                store_id,
                url,
                script,
                expected_url,
                require_nonempty,
                attempts,
            )
        )
        return self.result


def test_shopee_collector_requests_one_thailand_calendar_day(tmp_path):
    client = FakeCliClient(
        [
            {
                "campaign": "Shop GMV Max",
                "product": "Nike Body Spray ID: 54562829508",
                "metrics": [
                    "100",
                    "10",
                    "10%",
                    "฿37.00",
                    "฿190.00",
                    "2",
                ],
            }
        ]
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="27679338786521",
        ziniao_shopee_store_name="虾皮泰国",
    )

    metrics = ZiniaoCollector(
        settings, Platform.SHOPEE, cli_client=client
    ).collect(date(2026, 7, 22))

    assert len(metrics) == 1
    assert metrics[0].data_date == date(2026, 7, 22)
    _, url, script, expected_url, require_nonempty, _ = client.calls[0]
    assert "from=1784653200" in url
    assert "to=1784739599" in url
    assert expected_url == "from=1784653200&to=1784739599"
    assert require_nonempty is True
    assert "querySelectorAll" in script


def test_tiktok_collector_returns_empty_when_dashboard_has_no_campaigns(tmp_path):
    client = FakeCliClient([])
    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="27834942307818",
        ziniao_tiktok_store_name="泰国本土tk",
    )

    result = ZiniaoCollector(
        settings, Platform.TIKTOK, cli_client=client
    ).collect(date(2026, 7, 22))

    assert result == []
    assert "ads-creation/dashboard" in client.calls[0][1]


def test_shopee_collector_merges_all_paginated_product_rows(tmp_path):
    def row(sku):
        return {
            "campaign": "Shop GMV Max",
            "product": f"Product ID: {sku}",
            "metrics": ["100", "10", "10%", "฿10", "฿30", "1"],
        }

    class PagedClient:
        def __init__(self):
            self.next_clicks = 0

        def navigate_and_exec(self, *args, **kwargs):
            return {"page": 1, "total": 2, "rows": [row("101")]}

        def page_exec(self, store_id, script):
            self.next_clicks += 1
            return "clicked"

        def page_exec_until(self, store_id, script, *, ready, attempts=15):
            result = {"page": 2, "total": 2, "rows": [row("202")]}
            assert ready(result)
            return result

    client = PagedClient()
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="27679338786521",
        ziniao_shopee_store_name="虾皮泰国",
    )

    metrics = ZiniaoCollector(
        settings, Platform.SHOPEE, cli_client=client
    ).collect(date(2026, 7, 23))

    assert {metric.sku_id for metric in metrics} == {"101", "202"}
    assert client.next_clicks == 1


def test_shopee_collector_prefers_campaign_summary_over_partial_sku_rows(
    tmp_path,
):
    client = FakeCliClient(
        {
            "page": 1,
            "total": 2,
            "summary": {
                "campaign": "Shop GMV Max",
                "metrics": ["100", "10", "10%", "฿36.33", "฿231", "2"],
            },
            "rows": [
                {
                    "campaign": "Shop GMV Max",
                    "product": "Partial Product ID: 101",
                    "metrics": ["50", "5", "10%", "฿10", "฿30", "1"],
                }
            ],
        }
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="27679338786521",
        ziniao_shopee_store_name="虾皮泰国",
    )

    metrics = ZiniaoCollector(
        settings, Platform.SHOPEE, cli_client=client
    ).collect(date(2026, 7, 23))

    assert len(metrics) == 1
    assert metrics[0].sku_id == "__ALL__"
    assert metrics[0].spend == Decimal("36.33")

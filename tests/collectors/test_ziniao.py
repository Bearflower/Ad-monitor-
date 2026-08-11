from datetime import date
from decimal import Decimal

import pytest

from adwatch.collectors.ziniao import (
    ZiniaoCollector,
    ZiniaoNotConfigured,
    parse_shopee_campaign_summary,
    parse_shopee_overview,
    parse_shopee_product_rows,
    parse_tiktok_campaign_rows,
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


def test_parse_shopee_overview_uses_orders_instead_of_items_sold():
    metric = parse_shopee_overview(
        {
            "expense": "฿210.10",
            "sales": "฿179.00",
            "orders": "2",
            "items_sold": "3",
            "roas": "0.85",
        },
        store="虾皮泰国",
        account_id="27679338786521",
        data_date=date(2026, 7, 28),
    )

    assert metric.spend == Decimal("210.10")
    assert metric.attributed_gmv == Decimal("179.00")
    assert metric.orders == 2
    assert metric.roas == Decimal("0.8520")
    assert metric.campaign_id == "Shop GMV Max"


def test_parse_shopee_overview_accepts_display_roas_within_tolerance():
    metric = parse_shopee_overview(
        {
            "expense": "฿382",
            "sales": "฿1,782",
            "orders": "8",
            "items_sold": "8",
            "roas": "4.67",
        },
        store="虾皮泰国",
        account_id="27679338786521",
        data_date=date(2026, 8, 1),
    )

    assert metric.spend == Decimal(382)
    assert metric.attributed_gmv == Decimal(1782)
    assert metric.roas == Decimal("4.6649")


def test_parse_shopee_overview_rejects_inconsistent_roas():
    with pytest.raises(ValueError, match="ROAS consistency"):
        parse_shopee_overview(
            {
                "expense": "฿160.38",
                "sales": "฿179.00",
                "orders": "3",
                "items_sold": "3",
                "roas": "0.85",
            },
            store="虾皮泰国",
            account_id="27679338786521",
            data_date=date(2026, 7, 28),
        )


class FakeCliClient:
    def __init__(self, result, page_results=None):
        self.result = result
        self.page_results = page_results or []
        self.calls = []
        self.sleeper = lambda _s: None  # 测试中不等待

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

    def page_exec(self, store_id, script):
        self.calls.append(("page_exec", store_id, script[:80]))
        # SHOPEE_NEXT_PAGE_SCRIPT 是专门用于点击翻页按钮的短脚本
        if "button.click()" in script and "pager__button-next" in script:
            return "clicked" if self.page_results else "unavailable"
        # SHOPEE_DETAIL_PAGE_SCRIPT 是提取明细数据的长脚本
        if self.page_results:
            return self.page_results.pop(0)
        return {"rows": [], "hasNext": False, "page": 2}


def test_shopee_collector_requests_one_thailand_calendar_day(tmp_path):
    client = FakeCliClient(
        {
            "overview": {
                "expense": "฿37.00",
                "sales": "฿190.00",
                "orders": "2",
                "items_sold": "2",
                "roas": "5.14",
            },
            "rows": [],
            "page": 1,
            "totalPages": 1,
            "hasNext": False,
        }
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
    assert '"Expense"' in script
    assert '"Orders"' in script
    assert '"Items Sold"' in script


def test_shopee_collector_waits_for_overview_after_route_is_ready(tmp_path):
    client = FakeCliClient(
        {
            "overview": None,
            "rows": [],
            "page": 1,
            "totalPages": 1,
            "hasNext": False,
        },
        page_results=[
            {
                "overview": {
                    "expense": "฿191.95",
                    "sales": "฿319.00",
                    "orders": "3",
                    "items_sold": "3",
                    "roas": "1.66",
                },
                "rows": [],
                "page": 1,
                "totalPages": 1,
                "hasNext": False,
            }
        ],
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="27679338786521",
        ziniao_shopee_store_name="虾皮泰国",
    )

    metrics = ZiniaoCollector(
        settings, Platform.SHOPEE, cli_client=client
    ).collect(date(2026, 8, 4))

    assert len(metrics) == 1
    assert metrics[0].spend == Decimal("191.95")
    assert metrics[0].attributed_gmv == Decimal("319.00")


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


def test_parse_tiktok_campaign_rows_maps_real_campaign_metrics():
    metrics = parse_tiktok_campaign_rows(
        [
            {
                "campaign_id": "183746",
                "campaign": "GMV Max Product 1",
                "spend": "฿1,234.50",
                "gmv": "฿4,321.00",
                "orders": "18",
                "product_id": "749302",
                "currency": "THB",
            }
        ],
        store="泰国本土tk",
        account_id="27834942307818",
        data_date=date(2026, 7, 23),
    )

    assert len(metrics) == 1
    assert metrics[0].campaign_id == "183746"
    assert metrics[0].sku_id == "749302"
    assert metrics[0].spend == Decimal("1234.50")
    assert metrics[0].attributed_gmv == Decimal("4321.00")
    assert metrics[0].orders == 18


def test_tiktok_collector_uses_campaign_extraction_script(tmp_path):
    client = FakeCliClient(
        [
            {
                "campaign_id": "183746",
                "campaign": "GMV Max",
                "spend": "100",
                "gmv": "300",
                "orders": "4",
                "product_id": "",
                "currency": "THB",
            }
        ]
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_tiktok_store_id="27834942307818",
        ziniao_tiktok_store_name="泰国本土tk",
    )

    metrics = ZiniaoCollector(
        settings, Platform.TIKTOK, cli_client=client
    ).collect(date(2026, 7, 23))

    assert metrics[0].sku_id == "__ALL__"
    assert "campaign_id" in client.calls[0][2]


def test_shopee_collector_parses_overview_and_detail_rows(tmp_path):
    """测试同时解析概览和明细行，返回 overview + 明细 metric。"""
    client = FakeCliClient(
        {
            "overview": {
                "expense": "฿36.33",
                "sales": "฿231.00",
                "orders": "2",
                "items_sold": "2",
                "roas": "6.36",
            },
            "rows": [
                {
                    "campaign": "Shop GMV Max",
                    "campaign_id": "12345",
                    "product": "Partial Product",
                    "product_id": "101",
                    "spend": "฿10.00",
                    "gmv": "฿30.00",
                    "orders": "1",
                    "currency": "THB",
                }
            ],
            "page": 1,
            "totalPages": 2,
            "hasNext": True,
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

    assert len(metrics) == 2  # 1 overview + 1 detail row
    assert metrics[0].sku_id == "__ALL__"
    assert metrics[0].spend == Decimal("36.33")
    assert metrics[1].sku_id == "101"
    assert metrics[1].spend == Decimal("10.00")


def test_shopee_collector_returns_overview_and_rows_without_roas_validation(
    tmp_path,
):
    """测试概览和明细行同时返回，概览通过 ROAS 一致性校验。"""
    client = FakeCliClient(
        {
            "overview": {
                "expense": "฿210.10",
                "sales": "฿179.00",
                "orders": "2",
                "items_sold": "3",
                "roas": "0.85",
            },
            "rows": [
                {
                    "campaign": "Shop GMV Max",
                    "campaign_id": "12345",
                    "product": "Product A",
                    "product_id": "101",
                    "spend": "฿160.38",
                    "gmv": "฿179.00",
                    "orders": "3",
                    "currency": "THB",
                }
            ],
            "page": 1,
            "totalPages": 1,
            "hasNext": False,
        }
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="27679338786521",
        ziniao_shopee_store_name="虾皮泰国",
    )

    metrics = ZiniaoCollector(
        settings, Platform.SHOPEE, cli_client=client
    ).collect(date(2026, 7, 28))

    assert len(metrics) == 2  # 1 overview + 1 detail row
    assert metrics[0].spend == Decimal("210.10")
    assert metrics[0].orders == 2
    assert metrics[1].sku_id == "101"


def test_shopee_collector_parses_rows_without_overview(tmp_path):
    """测试无概览时，明细行仍能正确解析（不抛异常）。"""
    client = FakeCliClient(
        {
            "rows": [
                {
                    "campaign": "Shop GMV Max",
                    "campaign_id": "12345",
                    "product": "Product A",
                    "product_id": "101",
                    "spend": "฿160.38",
                    "gmv": "฿179.00",
                    "orders": "3",
                    "currency": "THB",
                }
            ],
            "page": 1,
            "totalPages": 1,
            "hasNext": False,
        }
    )
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="27679338786521",
        ziniao_shopee_store_name="虾皮泰国",
    )

    metrics = ZiniaoCollector(
        settings, Platform.SHOPEE, cli_client=client
    ).collect(date(2026, 7, 28))

    assert len(metrics) == 1
    assert metrics[0].sku_id == "101"
    assert metrics[0].spend == Decimal("160.38")


SHOPEE_DETAIL_FIXTURE = {
    "overview": {
        "expense": "฿1,234.56",
        "sales": "฿12,345.67",
        "orders": "123",
        "items_sold": "456",
        "roas": "10.0",
    },
    "rows": [
        {
            "campaign": "Shop GMV Max",
            "campaign_id": "12345",
            "product": "Product A",
            "product_id": "67890",
            "spend": "฿500.00",
            "gmv": "฿6,000.00",
            "orders": "50",
            "currency": "THB",
        },
        {
            "campaign": "Campaign B",
            "campaign_id": "54321",
            "product": "Product B",
            "product_id": "09876",
            "spend": "฿734.56",
            "gmv": "฿6,345.67",
            "orders": "73",
            "currency": "THB",
        },
    ],
    "page": 1,
    "totalPages": 3,
    "hasNext": True,
}


def test_parse_shopee_detail_rows():
    """测试明细行解析正确提取 campaign_id, sku_id, spend, gmv, orders。"""
    rows = SHOPEE_DETAIL_FIXTURE["rows"]
    result = parse_shopee_product_rows(
        rows,
        store="test_store",
        account_id="test_account",
        data_date=date(2026, 8, 6),
    )
    assert len(result) == 2
    assert result[0].campaign_id == "12345"
    assert result[0].sku_id == "67890"
    assert result[0].spend == Decimal("500.00")
    assert result[0].attributed_gmv == Decimal("6000.00")
    assert result[0].orders == 50
    assert result[1].campaign_id == "54321"
    assert result[1].sku_id == "09876"


def test_parse_shopee_detail_with_overview():
    """测试同时解析概览和明细行。"""
    overview = SHOPEE_DETAIL_FIXTURE["overview"]
    overview_metric = parse_shopee_overview(
        overview,
        store="test_store",
        account_id="test_account",
        data_date=date(2026, 8, 6),
    )
    assert overview_metric.campaign_id == "Shop GMV Max"
    assert overview_metric.sku_id == "__ALL__"
    assert overview_metric.spend == Decimal("1234.56")
    assert overview_metric.attributed_gmv == Decimal("12345.67")
    assert overview_metric.orders == 123


def test_shopee_collector_paginates_multiple_pages(tmp_path):
    """测试翻页采集：多页数据正确合并，翻页循环在无下一页时终止。"""
    first_page = {
        "overview": {
            "expense": "฿1,000.00",
            "sales": "฿10,000.00",
            "orders": "100",
            "items_sold": "150",
            "roas": "10.0",
        },
        "rows": [
            {
                "campaign": "Campaign A",
                "campaign_id": "111",
                "product": "Product A",
                "product_id": "AAA",
                "spend": "฿500.00",
                "gmv": "฿5,000.00",
                "orders": "50",
                "currency": "THB",
            }
        ],
        "page": 1,
        "totalPages": 2,
        "hasNext": True,
    }
    second_page = {
        "rows": [
            {
                "campaign": "Campaign B",
                "campaign_id": "222",
                "product": "Product B",
                "product_id": "BBB",
                "spend": "฿500.00",
                "gmv": "฿5,000.00",
                "orders": "50",
                "currency": "THB",
            }
        ],
        "page": 2,
        "totalPages": 2,
        "hasNext": False,
    }
    client = FakeCliClient(first_page, page_results=[second_page])
    settings = Settings(
        data_dir=tmp_path,
        ziniao_shopee_store_id="27679338786521",
        ziniao_shopee_store_name="虾皮泰国",
    )

    metrics = ZiniaoCollector(
        settings, Platform.SHOPEE, cli_client=client
    ).collect(date(2026, 7, 28))

    assert len(metrics) == 3  # 1 overview + 1 row (page 1) + 1 row (page 2)
    assert metrics[0].sku_id == "__ALL__"  # overview
    assert metrics[0].spend == Decimal("1000.00")
    assert metrics[1].sku_id == "AAA"  # page 1 detail
    assert metrics[2].sku_id == "BBB"  # page 2 detail
    # 应触发 1 次 navigate_and_exec + 2 次 page_exec（翻页点击 + 明细提取）
    exec_calls = [call for call in client.calls if call[0] == "page_exec"]
    assert len(exec_calls) == 2

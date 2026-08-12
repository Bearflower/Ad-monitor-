"""FallbackCollector 单元测试。

测试备用采集器的初始化、collect 方法和协议实现。
所有测试均 mock ZiniaoCliClient 以避免真实连接 Ziniao Bridge。
"""

from datetime import date
from unittest.mock import patch

import pytest

from adwatch.collectors.fallback import FallbackCollector
from adwatch.config import Settings
from adwatch.domain import Platform


@pytest.fixture(autouse=True)
def _mock_ziniao_client():
    """自动 mock ZiniaoCliClient，避免真实连接 Ziniao Bridge。

    默认设置 _store_ids 为 None，使 _collect_shopee 直接返回空列表。
    """
    with patch("adwatch.collectors.fallback.ZiniaoCliClient") as mock:
        instance = mock.return_value
        instance._store_ids = None
        yield


class TestFallbackCollectorInit:
    """FallbackCollector 初始化测试。"""

    def test_fallback_collector_platform(self) -> None:
        """构造时传入的 platform 正确设置。"""
        collector = FallbackCollector("shopee")
        assert collector.platform == Platform.SHOPEE

        collector = FallbackCollector("tiktok")
        assert collector.platform == Platform.TIKTOK

    def test_fallback_collector_source(self) -> None:
        """source 属性返回 'fallback-{platform}'。"""
        collector = FallbackCollector("shopee")
        assert collector.source == "fallback-shopee"

        collector = FallbackCollector("tiktok")
        assert collector.source == "fallback-tiktok"


class TestFallbackCollectorCollect:
    """FallbackCollector.collect 方法测试。"""

    def test_collect_shopee_no_store_ids(self, tmp_path) -> None:
        """Shopee 没有 store_ids 时返回空列表。"""
        collector = FallbackCollector(
            "shopee", settings=Settings(data_dir=tmp_path)
        )
        result = collector.collect(date(2026, 8, 10))
        assert result == []

    def test_collect_tiktok_returns_empty(self) -> None:
        """TikTok 始终返回空列表（备用采集暂未实现）。"""
        collector = FallbackCollector("tiktok")
        result = collector.collect(date(2026, 8, 10))
        assert result == []

    def test_collect_shopee_reads_configured_store_overview(self, tmp_path) -> None:
        class Client:
            def __init__(self):
                self.calls = []

            def navigate_and_exec(self, store_id, url, script, **kwargs):
                self.calls.append((store_id, url, script, kwargs))
                return {
                    "overview": {
                        "expense": "฿300.00",
                        "sales": "฿655.00",
                        "orders": "5",
                        "items_sold": "5",
                        "roas": "2.18",
                    }
                }

        client = Client()
        settings = Settings(
            data_dir=tmp_path,
            ziniao_shopee_store_id="27679338786521",
            ziniao_shopee_store_name="虾皮泰国",
        )
        collector = FallbackCollector(
            "shopee", settings=settings, cli_client=client
        )

        metrics = collector.collect(date(2026, 8, 10))

        assert len(metrics) == 1
        assert metrics[0].store == "虾皮泰国"
        assert metrics[0].account_id == "27679338786521"
        assert metrics[0].spend == 300
        assert metrics[0].attributed_gmv == 655
        assert metrics[0].orders == 5
        assert metrics[0].source == "fallback-shopee"
        assert client.calls[0][0] == "27679338786521"
        assert "from=" in client.calls[0][1]
        assert "table.eds-table__body" in client.calls[0][2]
        assert client.calls[0][3]["require_nonempty"] is True


class TestFallbackCollectorProtocol:
    """FallbackCollector 实现 Collector 协议的验证。"""

    def test_fallback_collector_is_collector(self) -> None:
        """FallbackCollector 实现了 Collector 协议。

        Collector 是 typing.Protocol，因此使用 hasattr 检查
        协议要求的全部属性和方法是否存在。
        """
        collector = FallbackCollector("shopee")
        assert hasattr(collector, "platform")
        assert hasattr(collector, "source")
        assert hasattr(collector, "collect")
        assert callable(collector.collect)

    def test_fallback_collector_has_platform_property(self) -> None:
        """collector 有 platform 属性。"""
        collector = FallbackCollector("shopee")
        assert hasattr(collector, "platform")

    def test_fallback_collector_has_source_property(self) -> None:
        """collector 有 source 属性。"""
        collector = FallbackCollector("shopee")
        assert hasattr(collector, "source")

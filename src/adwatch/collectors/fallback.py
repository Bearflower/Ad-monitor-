from __future__ import annotations

from dataclasses import replace
from datetime import date

from adwatch.collectors.base import Collector
from adwatch.collectors.ziniao_client import ZiniaoCliClient
from adwatch.config import Settings
from adwatch.domain import DailyAdMetric, Platform

SHOPEE_TABLE_OVERVIEW_SCRIPT = r"""
JSON.stringify((()=>{
  const tables=Array.from(document.querySelectorAll("table.eds-table__body"));
  const metricTable=tables.find(table=>{
    const row=table.querySelector("tbody tr");
    return !!row && row.querySelectorAll("td").length>=8
      && /[฿$¥]|(?:THB|USD|CNY)/i.test(row.innerText||"");
  });
  if(!metricTable)return null;
  const values=Array.from(
    metricTable.querySelector("tbody tr").querySelectorAll("td")
  ).map(cell=>(cell.innerText||cell.textContent||"").trim().replace(/\s+/g," "));
  if(values.length<8)return null;
  return {overview:{
    expense:values[3], sales:values[4], orders:values[5],
    items_sold:values[6], roas:values[7]
  }};
})())
""".strip()


class FallbackCollector(Collector):
    """Read only the Shopee overview through the low-level page bridge.

    The primary collector parses split campaign/SKU tables and paginates. This
    collector deliberately uses the smaller, independent overview script so a
    detail-table or pagination regression does not remove the store KPI.
    """

    def __init__(
        self,
        platform: str,
        *,
        settings: Settings | None = None,
        cli_client: ZiniaoCliClient | None = None,
    ) -> None:
        self._platform = Platform(platform)
        self.settings = settings or Settings.from_env()
        self._client = cli_client or ZiniaoCliClient()

    @property
    def platform(self) -> Platform:
        return self._platform

    @property
    def source(self) -> str:
        return f"fallback-{self._platform.value}"

    def collect(self, data_date: date) -> list[DailyAdMetric]:
        if self._platform is Platform.SHOPEE:
            return self._collect_shopee(data_date)
        return []

    def _collect_shopee(self, data_date: date) -> list[DailyAdMetric]:
        from adwatch.collectors.ziniao import (
            _thailand_day_timestamps,
            parse_shopee_overview,
        )

        store_id = self.settings.ziniao_shopee_store_id
        if not store_id:
            return []
        start, end = _thailand_day_timestamps(data_date)
        expected = f"from={start}&to={end}"
        url = (
            "https://seller.shopee.co.th/portal/marketing/pas/index"
            f"?from={start}&to={end}&type=new_cpc_homepage&group=custom"
        )
        result = self._client.navigate_and_exec(
            store_id,
            url,
            SHOPEE_TABLE_OVERVIEW_SCRIPT,
            expected_url=expected,
            require_nonempty=True,
        )
        if not isinstance(result, dict) or not isinstance(
            result.get("overview"), dict
        ):
            raise TypeError("Shopee fallback overview is incomplete")
        metric = parse_shopee_overview(
            result["overview"],
            store=self.settings.ziniao_shopee_store_name,
            account_id=store_id,
            data_date=data_date,
        )
        return [replace(metric, source=self.source)]

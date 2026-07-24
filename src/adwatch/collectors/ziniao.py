import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from adwatch.collectors.ziniao_client import ZiniaoCliClient
from adwatch.config import Settings
from adwatch.domain import DailyAdMetric, Platform


class ZiniaoNotConfigured(RuntimeError):
    pass


def _primary_decimal(value: str) -> Decimal:
    normalized = value.strip().split()[0].replace("฿", "").replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError(f"Invalid Shopee metric value: {value}") from error


def parse_shopee_product_rows(
    rows: list[dict[str, object]],
    *,
    store: str,
    account_id: str,
    data_date: date,
) -> list[DailyAdMetric]:
    metrics = []
    for row in rows:
        product = str(row.get("product", ""))
        product_id = re.search(r"\bID:\s*(\d+)", product)
        values = row.get("metrics", [])
        if not product_id or not isinstance(values, list) or len(values) < 6:
            continue
        metrics.append(
            DailyAdMetric(
                platform=Platform.SHOPEE,
                store=store,
                account_id=account_id,
                campaign_id=str(row.get("campaign", "Shopee Ads")),
                sku_id=product_id.group(1),
                data_date=data_date,
                currency="THB",
                spend=_primary_decimal(str(values[3])),
                attributed_gmv=_primary_decimal(str(values[4])),
                orders=int(_primary_decimal(str(values[5]))),
                source="ziniao-cli",
            )
        )
    return metrics


def parse_shopee_campaign_summary(
    summary: dict[str, object],
    *,
    store: str,
    account_id: str,
    data_date: date,
) -> DailyAdMetric:
    values = summary.get("metrics", [])
    if not isinstance(values, list) or len(values) < 6:
        raise ValueError("Shopee campaign summary is incomplete")
    return DailyAdMetric(
        platform=Platform.SHOPEE,
        store=store,
        account_id=account_id,
        campaign_id=str(summary.get("campaign", "Shop GMV Max")),
        sku_id="__ALL__",
        data_date=data_date,
        currency="THB",
        spend=_primary_decimal(str(values[3])),
        attributed_gmv=_primary_decimal(str(values[4])),
        orders=int(_primary_decimal(str(values[5]))),
        source="ziniao-cli",
    )


class ZiniaoCollector:
    source = "ziniao"

    def __init__(
        self,
        settings: Settings,
        platform: Platform,
        *,
        cli_client: ZiniaoCliClient | None = None,
    ) -> None:
        self.settings = settings
        self.platform = platform
        self.cli_client = cli_client or ZiniaoCliClient()

    def collect(self, data_date: date) -> list[DailyAdMetric]:
        store_id = self._store_id
        if not store_id:
            raise ZiniaoNotConfigured(
                f"Ziniao collection requires {self._store_id_env}"
            )
        if self.platform is Platform.TIKTOK:
            result = self.cli_client.navigate_and_exec(
                store_id,
                "https://seller-th.tiktok.com/ads-creation/dashboard",
                "JSON.stringify([])",
                expected_url="/ads-creation/dashboard",
            )
            if not isinstance(result, list):
                raise ValueError("TikTok Ads page returned an invalid result")
            return []

        start, end = _thailand_day_timestamps(data_date)
        expected = f"from={start}&to={end}"
        url = (
            "https://seller.shopee.co.th/portal/marketing/pas/index"
            f"?from={start}&to={end}&type=new_cpc_homepage&group=custom"
        )
        first_page = self.cli_client.navigate_and_exec(
            store_id,
            url,
            SHOPEE_PAGE_SCRIPT,
            expected_url=expected,
            require_nonempty=True,
        )
        if isinstance(first_page, list):
            rows = first_page
        elif isinstance(first_page, dict):
            summary = first_page.get("summary")
            if isinstance(summary, dict):
                return [
                    parse_shopee_campaign_summary(
                        summary,
                        store=self.settings.ziniao_shopee_store_name,
                        account_id=store_id,
                        data_date=data_date,
                    )
                ]
            rows = list(first_page.get("rows", []))
            current_page = int(first_page.get("page", 1))
            total_pages = int(first_page.get("total", 1))
            while current_page < total_pages:
                self.cli_client.page_exec(store_id, SHOPEE_NEXT_PAGE_SCRIPT)
                next_page = self.cli_client.page_exec_until(
                    store_id,
                    SHOPEE_PAGE_SCRIPT,
                    ready=lambda value, previous=current_page: (
                        isinstance(value, dict)
                        and int(value.get("page", 0)) > previous
                        and bool(value.get("rows"))
                    ),
                )
                rows.extend(next_page["rows"])
                current_page = int(next_page["page"])
                total_pages = int(next_page["total"])
        else:
            raise ValueError("Shopee Ads page returned an invalid result")
        return parse_shopee_product_rows(
            rows,
            store=self.settings.ziniao_shopee_store_name,
            account_id=store_id,
            data_date=data_date,
        )

    @property
    def _store_id(self) -> str:
        if self.platform is Platform.TIKTOK:
            return self.settings.ziniao_tiktok_store_id
        return self.settings.ziniao_shopee_store_id

    @property
    def _store_id_env(self) -> str:
        if self.platform is Platform.TIKTOK:
            return "ZINIAO_TIKTOK_STORE_ID"
        return "ZINIAO_SHOPEE_STORE_ID"


def _thailand_day_timestamps(data_date: date) -> tuple[int, int]:
    timezone = ZoneInfo("Asia/Bangkok")
    start = datetime.combine(data_date, time.min, timezone)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return int(start.timestamp()), int(end.timestamp())


SHOPEE_PAGE_SCRIPT = r"""
JSON.stringify((()=>{
  const size=document.querySelector(".eds-pagination-sizes__content");
  if(size&&!size.textContent.trim().startsWith("50")){
    size.click();
    const option=Array.from(
      document.querySelectorAll(".eds-pagination-sizes__popper li")
    ).find(e=>(e.innerText||e.textContent||"").trim()==="50");
    if(option) option.click();
    return null;
  }
  const tables=Array.from(document.querySelectorAll("table"));
  const rows=t=>Array.from(t.querySelectorAll("tbody tr")).map(r=>
    Array.from(r.querySelectorAll("td")).map(c=>
      (c.innerText||c.textContent||"").trim().replace(/\s+/g," ")
    )
  );
  const nameRows=tables.map(rows).find(rs=>rs.some(r=>r.join(" ").includes("ID:")))||[];
  const metricRows=tables.map(rows).find(rs=>
    rs.length===nameRows.length&&rs.some(r=>r.length>=6)
  )||[];
  const page=Number(
    document.querySelector(".eds-pager__current")?.textContent||1
  );
  const total=Number(
    document.querySelector(".eds-pager__total")?.textContent||1
  );
  const productRows=nameRows.map((r,i)=>({
      campaign:"Shop GMV Max",
      product:r.join(" "),
      metrics:metricRows[i]||[]
    })).filter(r=>r.product.includes("ID:"));
  const summary=nameRows.length&&metricRows.length?{
    campaign:"Shop GMV Max",
    metrics:metricRows[0]||[]
  }:null;
  return productRows.length?{page,total,summary,rows:productRows}:null;
})())
""".strip()


SHOPEE_NEXT_PAGE_SCRIPT = r"""
(()=>{
  const button=document.querySelector(".eds-pager__button-next");
  if(!button||button.disabled)return "unavailable";
  button.click();
  return "clicked";
})()
""".strip()

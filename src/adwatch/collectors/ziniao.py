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


def parse_shopee_overview(
    overview: dict[str, object],
    *,
    store: str,
    account_id: str,
    data_date: date,
) -> DailyAdMetric:
    spend = _primary_decimal(str(overview.get("expense", "")))
    attributed_gmv = _primary_decimal(str(overview.get("sales", "")))
    orders = int(_primary_decimal(str(overview.get("orders", ""))))
    reported_roas = _primary_decimal(str(overview.get("roas", "")))
    calculated_roas = (
        attributed_gmv / spend if spend > 0 else Decimal(0)
    ).quantize(Decimal("0.01"))
    if calculated_roas != reported_roas.quantize(Decimal("0.01")):
        raise ValueError(
            "Shopee overview failed ROAS consistency check: "
            f"sales={attributed_gmv}, expense={spend}, "
            f"reported={reported_roas}, calculated={calculated_roas}"
        )
    return DailyAdMetric(
        platform=Platform.SHOPEE,
        store=store,
        account_id=account_id,
        campaign_id="Shop GMV Max",
        sku_id="__ALL__",
        data_date=data_date,
        currency="THB",
        spend=spend,
        attributed_gmv=attributed_gmv,
        orders=orders,
        source="ziniao-cli",
    )


def parse_tiktok_campaign_rows(
    rows: list[dict[str, object]],
    *,
    store: str,
    account_id: str,
    data_date: date,
) -> list[DailyAdMetric]:
    metrics = []
    for row in rows:
        campaign_id = str(row.get("campaign_id", "")).strip()
        if not campaign_id:
            continue
        product_id = str(row.get("product_id", "")).strip() or "__ALL__"
        metrics.append(
            DailyAdMetric(
                platform=Platform.TIKTOK,
                store=store,
                account_id=account_id,
                campaign_id=campaign_id,
                sku_id=product_id,
                data_date=data_date,
                currency=str(row.get("currency", "THB")).strip() or "THB",
                spend=_primary_decimal(str(row.get("spend", "0"))),
                attributed_gmv=_primary_decimal(str(row.get("gmv", "0"))),
                orders=int(_primary_decimal(str(row.get("orders", "0")))),
                source="ziniao-cli",
            )
        )
    return metrics


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
                TIKTOK_PAGE_SCRIPT,
                expected_url="/ads-creation/dashboard",
            )
            if not isinstance(result, list):
                raise ValueError("TikTok Ads page returned an invalid result")
            return parse_tiktok_campaign_rows(
                result,
                store=self.settings.ziniao_tiktok_store_name,
                account_id=store_id,
                data_date=data_date,
            )

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
            overview = first_page.get("overview")
            if isinstance(overview, dict):
                return [
                    parse_shopee_overview(
                        overview,
                        store=self.settings.ziniao_shopee_store_name,
                        account_id=store_id,
                        data_date=data_date,
                    )
                ]
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
            raise ValueError(
                "Shopee Ads page did not expose a labeled overview"
            )
        else:
            raise TypeError("Shopee Ads page returned an invalid result")
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
  const lines=(document.body.innerText||"").split(/\n/).map(
    value=>value.trim().replace(/\s+/g," ")
  ).filter(Boolean);
  const valueAfter=labels=>{
    const index=lines.findIndex(line=>labels.includes(line));
    return index>=0?lines[index+1]||"":null;
  };
  const overview={
    expense:valueAfter(["Expense","ค่าใช้จ่าย"]),
    sales:valueAfter(["Sales","ยอดขาย"]),
    orders:valueAfter(["Orders","คำสั่งซื้อ"]),
    items_sold:valueAfter(["Items Sold","สินค้าที่ขายได้"]),
    roas:valueAfter(["ROAS"])
  };
  const hasOverview=Object.values(overview).every(value=>value!==null);
  return hasOverview?{overview}:null;
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


TIKTOK_PAGE_SCRIPT = r"""
JSON.stringify((()=>{
  const rows=Array.from(document.querySelectorAll("table tbody tr"));
  return rows.map(row=>{
    const cells=Array.from(row.querySelectorAll("td")).map(cell=>
      (cell.innerText||cell.textContent||"").trim().replace(/\s+/g," ")
    );
    const text=cells.join(" ");
    const id=text.match(/(?:Campaign\s*ID|ID)\s*[:：]\s*(\d+)/i);
    if(!id)return null;
    const money=cells.filter(value=>/[฿$¥]|(?:THB|USD|CNY)/i.test(value));
    const orders=cells.find(value=>/^\d+$/.test(value.replace(/,/g,"")));
    const product=text.match(/(?:Product\s*ID|商品ID)\s*[:：]\s*(\d+)/i);
    return {
      campaign_id:id[1],
      campaign:cells[0]||id[1],
      product_id:product?product[1]:"",
      spend:money[0]||"0",
      gmv:money[1]||"0",
      orders:orders||"0",
      currency:/USD/i.test(text)?"USD":(/CNY|¥/.test(text)?"CNY":"THB")
    };
  }).filter(Boolean);
})())
""".strip()

"""Shopee Ads 的紫鸟只读会话与页面契约解析。"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as clock_time
from decimal import Decimal, InvalidOperation
from typing import ClassVar
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from adwatch.config import Settings
from adwatch.optimizer.models import (
    CampaignSnapshot,
    CampaignStatus,
    normalize_currency,
)


class ZiniaoCliError(RuntimeError):
    """紫鸟 CLI 的无敏感信息失败。"""


class ZiniaoAuthenticationError(ZiniaoCliError):
    """紫鸟 CLI 认证失败；不等同于 Shopee 页面登录失效。"""


class BridgeUnavailableError(ZiniaoCliError):
    """本地 ZClaw Bridge 不可用或未在重试窗口内就绪。"""


class StoreNotFoundError(ZiniaoCliError):
    """配置的店铺不在 Bridge 店铺列表中。"""


class StorePlatformMismatchError(ZiniaoCliError):
    """配置店铺并非 Shopee 店铺。"""


class StoreNameMismatchError(ZiniaoCliError):
    """Bridge 返回的店铺名称与配置期望不一致。"""


class ShopeeLoginExpiredError(ZiniaoCliError):
    """Shopee Seller Centre 会话已失效。"""


class PageTimeoutError(ZiniaoCliError):
    """只读页面访问或提取超时。"""


@dataclass(frozen=True)
class ShopeeSessionReady:
    """经过平台、店铺名与登录页检查后的可用只读会话。"""

    store_id: str
    store_name: str
    reused: bool
    observed_at: datetime


@dataclass(frozen=True)
class ShopeeAdsPage:
    """Shopee Ads 页面原始契约及可用于后续采集的控制面快照。"""

    data_date: date
    observed_at: datetime
    campaigns: tuple[CampaignSnapshot, ...]
    missing_fields: tuple[str, ...]
    stable_id_evidence: Mapping[str, object]
    pagination: Mapping[str, object]
    raw_contract: Mapping[str, object]
    diagnostics: tuple[str, ...]


class ZiniaoCliClient:
    """仅封装白名单内的 ziniao-cli 只读命令。"""

    _SHOPEE_ADS_PATH = "/portal/marketing/pas/index"
    _SHOPEE_ADS_URL = f"https://seller.shopee.co.th{_SHOPEE_ADS_PATH}"
    _READ_ONLY_SCRIPTS: ClassVar[dict[str, str]] = {
        "session_status": """JSON.stringify({
          url: location.href,
          title: document.title,
          text: (document.body.innerText || '').slice(0, 600)
        })""",
        "shopee_ads_contract": """JSON.stringify((()=>{
          const tables=Array.from(document.querySelectorAll('table'));
          const metricHeaderTable=tables.find(value=>/Daily Budget|Target ROAS/.test(
            value.innerText || ''
          ));
          const pager=document.querySelector('.eds-pager');
          const headers=metricHeaderTable?Array.from(
            metricHeaderTable.querySelectorAll('thead th')
          ).map(
            cell=>(cell.innerText||cell.textContent||'').trim()
          ):[];
          const valueAt=(cells, names)=>{
            const index=headers.findIndex(header=>names.includes(header));
            return index>=0?cells[index]||null:null;
          };
          const primary=value=>value?String(value).split('\\n')[0].trim()||null:null;
          const bodyTables=tables.filter(value=>value.querySelectorAll('tbody tr').length);
          const infoTable=bodyTables.find(value=>
            /Product Ads|Shop\\+ Ads|Live Ads/i.test(value.innerText||'')
          );
          const infoRows=infoTable?Array.from(infoTable.querySelectorAll('tbody tr')):[];
          const metricTable=bodyTables.find(value=>{
            const bodyRows=value.querySelectorAll('tbody tr');
            const first=bodyRows[0];
            return value!==infoTable&&bodyRows.length===infoRows.length&&first&&
              first.querySelectorAll('td').length>=headers.length;
          });
          const metricRows=metricTable?Array.from(
            metricTable.querySelectorAll('tbody tr')
          ):[];
          const rows=infoRows.map((infoRow,index)=>{
            const infoCells=Array.from(infoRow.querySelectorAll('td')).map(cell=>
              (cell.innerText||cell.textContent||'').trim()
            );
            const metricRow=metricRows[index];
            const metricCells=metricRow?Array.from(metricRow.querySelectorAll('td')).map(
              cell=>(cell.innerText||cell.textContent||'').trim()
            ):[];
            const adInfo=infoCells.join('\\n');
            const lines=adInfo.split('\\n').map(value=>value.trim()).filter(Boolean);
            const detail=infoRow.querySelector(
              'a[href*="/portal/marketing/pas/"]'
            );
            const detailHref=detail?.getAttribute('href')||null;
            const detailId=(detailHref?.match(
              /\\/portal\\/marketing\\/pas\\/[^?#]*\\/([A-Za-z0-9_-]+)(?:\\?|$)/
            )||[])[1]||null;
            const typeText=lines.find(value=>
              /Product Ads|Shop\\+ Ads|Live Ads/i.test(value)
            )||infoRow.dataset.adType||null;
            const statusText=infoRow.dataset.status||
              (adInfo.match(/\\b(Scheduled|Ongoing|Paused|Ended|Deleted|Active)\\b/i)||
                [])[1]||null;
            return {
              page_campaign_id:infoRow.dataset.campaignId||detailId,
              detail_href:detailHref,
              item_id:infoRow.dataset.itemId||null,
              name:lines[0]||null,
              type:typeText,
              status:statusText,
              current_budget:primary(valueAt(metricCells,['Daily Budget','Budget'])),
              target_roas:primary(valueAt(metricCells,['Target ROAS'])),
              spend:primary(valueAt(metricCells,['Expense','Spend','Cost'])),
              gmv:primary(valueAt(metricCells,['Sales','GMV'])),
              orders:primary(valueAt(metricCells,['Conversions','Orders'])),
              reported_roas:primary(valueAt(metricCells,['ROAS'])),
              reported_cpa:primary(valueAt(metricCells,['Cost per Conversion','CPA'])),
              currency:((metricCells.join(' ').match(
                /(?:THB|USD|CNY|฿|[$]|¥)/i
              )||[])[0])||null
            };
          });
          const overviewNode=document.querySelector('[data-ads-overview]');
          const metricCards=Object.fromEntries(Array.from(
            document.querySelectorAll('.metric-item')
          ).map(card=>[
            (card.querySelector('.title')?.textContent||'').trim(),
            (card.querySelector('.text.ellipsis')?.textContent||'').trim()
          ]).filter(item=>item[0]&&item[1]));
          const overview=overviewNode?{
            currency:overviewNode.dataset.currency||null,
            spend:overviewNode.dataset.spend||null,
            gmv:overviewNode.dataset.gmv||null,
            orders:overviewNode.dataset.orders||null,
            reported_roas:overviewNode.dataset.roas||null,
            reported_cpa:overviewNode.dataset.cpa||null
          }:metricCards.Expense&&metricCards.Sales&&metricCards.Orders?{
            currency:(metricCards.Expense.match(/(?:THB|USD|CNY|฿|[$]|¥)/i)||[])[0]||null,
            spend:metricCards.Expense,
            gmv:metricCards.Sales,
            orders:metricCards.Orders,
            reported_roas:metricCards.ROAS||null,
            reported_cpa:null
          }:null;
          const productRows=[];
          const activeListTab=document.querySelector(
            '.ads-list-tabs .eds-tabs__nav-tab.active .ads-list-tab-label'
          );
          const activeListText=(activeListTab?.textContent||'').trim();
          const required=['page_campaign_id','detail_href','name','type','status',
            'spend','gmv','orders'];
          const missing=rows.length===0?['campaign_rows',...required]:required.filter(
            field=>rows.some(row=>row[field]===null||row[field]===undefined||row[field]==='')
          );
          return {
            captured_at:new Date().toISOString(),
            page_url:location.href,
            url:location.href,
            title:document.title,
            columns:headers,
            overview,
            campaign_rows:rows,
            product_rows:productRows,
            ads_list_view:activeListText.startsWith('Shop GMV Max')?
              'shop_gmv_max':'individual_ads',
            has_shop_gmv_max:Array.from(
              document.querySelectorAll('.ads-list-tab-label')
            ).some(value=>(value.textContent||'').trim().startsWith('Shop GMV Max')),
            missing_fields:missing,
            stable_id_evidence:{
              preferred_order:['page_campaign_id','detail_href','store_id+type+item_id']
            },
            pagination:{
              page:pager?.querySelector('.eds-pager__current')?.textContent||null,
              total_pages:pager?.querySelector('.eds-pager__total')?.textContent||null,
              has_next:!(pager?.querySelector('.eds-pager__button-next')?.disabled??true)
            }
          };
        })())""",
    }
    _READ_ONLY_SCRIPTS["shopee_ads_contract_page"] = _READ_ONLY_SCRIPTS[
        "shopee_ads_contract"
    ]
    _READ_ONLY_SCRIPTS["shopee_shop_gmv_max_contract"] = """JSON.stringify((()=>{
      const tables=Array.from(document.querySelectorAll('table'));
      const headerTable=tables.find(value=>Array.from(
        value.querySelectorAll('thead th')
      ).some(cell=>(cell.innerText||cell.textContent||'').trim()==='Ad product'));
      const metricHeaderTable=tables.find(value=>{
        const text=Array.from(value.querySelectorAll('thead th')).map(
          cell=>(cell.innerText||cell.textContent||'').trim()
        );
        return text.includes('Expense')&&text.includes('Sales')&&
          !text.includes('Daily Budget');
      });
      const headers=metricHeaderTable?Array.from(
        metricHeaderTable.querySelectorAll('thead th')
      ).map(cell=>(cell.innerText||cell.textContent||'').trim()):[];
      const bodyTables=tables.filter(value=>value.querySelectorAll('tbody tr').length);
      const infoTable=headerTable?bodyTables.find(value=>
        value.querySelector('.overall-performance-left-info')
      ):null;
      const infoRows=infoTable?Array.from(infoTable.querySelectorAll('tbody tr')):[];
      const metricTable=bodyTables.find(value=>{
        const rows=value.querySelectorAll('tbody tr');
        const first=rows[0];
        return value!==infoTable&&rows.length===infoRows.length&&first&&
          first.querySelectorAll('td').length===headers.length;
      });
      const metricRow=metricTable?.querySelector('tbody tr');
      const metricCells=metricRow?Array.from(metricRow.querySelectorAll('td')).map(
        cell=>(cell.innerText||cell.textContent||'').trim()
      ):[];
      const valueAt=name=>{
        const index=headers.indexOf(name);
        const value=index>=0?metricCells[index]||null:null;
        return value?String(value).split('\\n')[0].trim()||null:null;
      };
      const campaignRow=infoRows.length&&metricRow?{
        item_id:'__SHOP_GMV_MAX__',
        name:'Shop GMV Max',
        type:'shop_gmv_max',
        status:'Ongoing',
        current_budget:null,
        target_roas:null,
        spend:valueAt('Expense'),
        gmv:valueAt('Sales'),
        orders:valueAt('Conversions'),
        reported_roas:valueAt('ROAS'),
        reported_cpa:valueAt('Cost per Conversion'),
        currency:((metricCells.join(' ').match(
          /(?:THB|USD|CNY|฿|[$]|¥)/i
        )||[])[0])||null
      }:null;
      const required=['spend','gmv','orders'];
      return {
        captured_at:new Date().toISOString(),
        campaign_row:campaignRow,
        missing_fields:campaignRow?required.filter(
          field=>campaignRow[field]===null||campaignRow[field]===undefined||
            campaignRow[field]===''
        ):['shop_gmv_max']
      };
    })())"""

    def __init__(
        self,
        *,
        executable: str = "ziniao-cli",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        settings: Settings | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.executable = executable
        self.runner = runner
        configured = settings or Settings.from_env()
        self.settings = configured
        self.timeout_seconds = (
            configured.ziniao_cli_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )

    def store_list(self) -> list[dict[str, object]]:
        response = self._run(
            "store_list", ["store", "list", "--all", "--format", "json"]
        )
        data = response.get("data", {})
        if isinstance(data, dict):
            items = data.get("items", [])
        else:
            items = data
        return (
            [item for item in items if isinstance(item, dict)]
            if isinstance(items, list)
            else []
        )

    def store_open(self, store_id: str) -> dict[str, object]:
        self._require_store_id(store_id)
        response = self._run(
            "store_open",
            [
                "store",
                "open",
                "--id",
                store_id,
                "--url",
                self._SHOPEE_ADS_URL,
            ],
        )
        data = response.get("data", {})
        return data if isinstance(data, dict) else {}

    def page_exec(self, store_id: str, script_name: str) -> object:
        self._require_store_id(store_id)
        script = self._READ_ONLY_SCRIPTS.get(script_name)
        if script is None:
            raise ValueError("page_exec accepts approved read-only scripts only")
        response = self._run(
            "page_exec", ["page", "exec", "--store-id", store_id, "--script", script]
        )
        data = response.get("data", {})
        nested = data.get("data", {}) if isinstance(data, dict) else {}
        result = nested.get("result") if isinstance(nested, dict) else None
        if not isinstance(result, str):
            return result
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result

    def page_next(self, store_id: str) -> None:
        """只允许点击 Shopee 列表的固定下一页控件。"""
        self._require_store_id(store_id)
        self._run(
            "page_next",
            [
                "page",
                "click",
                "--store-id",
                store_id,
                "--selector",
                ".eds-pager__button-next",
            ],
        )

    def page_shop_gmv_max(self, store_id: str) -> None:
        """只允许切换到 Shopee 的固定 Shop GMV Max 子页签。"""
        self._require_store_id(store_id)
        self._run(
            "page_shop_gmv_max",
            [
                "page",
                "click",
                "--store-id",
                store_id,
                "--selector",
                ".ads-list-tabs .eds-tabs__nav-tab:nth-child(2)",
            ],
        )

    def page_individual_ads(self, store_id: str) -> None:
        """只允许切回 Shopee 的固定广告组与单品广告子页签。"""
        self._require_store_id(store_id)
        self._run(
            "page_individual_ads",
            [
                "page",
                "click",
                "--store-id",
                store_id,
                "--selector",
                ".ads-list-tabs .eds-tabs__nav-tab:nth-child(1)",
            ],
        )

    def visit_shopee_ads(self, store_id: str, data_date: date) -> str:
        """只允许导航到固定的 Shopee Ads 页面，并带上业务日过滤。"""
        return self.visit_shopee_ads_page(store_id, data_date, 1)

    def visit_shopee_ads_page(self, store_id: str, data_date: date, page: int) -> str:
        """固定 Shopee Ads URL 的业务日及页码；不接受任意 URL。"""
        self._require_store_id(store_id)
        if isinstance(data_date, datetime) or not isinstance(data_date, date):
            raise TypeError("data_date must be a date")
        if isinstance(page, bool) or not isinstance(page, int) or page <= 0:
            raise ValueError("page must be a positive integer")
        timezone = ZoneInfo("Asia/Bangkok")
        start = datetime.combine(data_date, clock_time.min, timezone)
        end = datetime.combine(data_date, clock_time.max, timezone)
        url = (
            f"https://seller.shopee.co.th{self._SHOPEE_ADS_PATH}"
            f"?from={int(start.timestamp())}&to={int(end.timestamp())}"
            f"&type=new_cpc_homepage&group=custom&page={page}"
        )
        self._run(
            "page_visit",
            [
                "page",
                "visit",
                "--store-id",
                store_id,
                "--url",
                url,
                "--wait-until",
                "networkidle",
            ],
        )
        return url

    def _run(self, operation: str, arguments: list[str]) -> dict[str, object]:
        allowed = {
            "store_list": ("store", "list"),
            "store_open": ("store", "open"),
            "page_exec": ("page", "exec"),
            "page_visit": ("page", "visit"),
            "page_next": ("page", "click"),
            "page_shop_gmv_max": ("page", "click"),
            "page_individual_ads": ("page", "click"),
        }
        if tuple(arguments[:2]) != allowed.get(operation):
            raise ValueError("unsupported ziniao CLI operation")
        command = [self.executable, *arguments]
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            if operation.startswith("page_"):
                raise PageTimeoutError("Shopee 页面只读操作超时") from None
            raise BridgeUnavailableError("ZClaw Bridge 操作超时") from None
        except OSError:
            raise BridgeUnavailableError("ZClaw Bridge 不可用") from None
        response = self._structured_response(completed)
        if response is None:
            if completed.returncode:
                raise ZiniaoCliError("Ziniao CLI command failed") from None
            if operation in {
                "page_visit",
                "page_next",
                "page_shop_gmv_max",
                "page_individual_ads",
            }:
                return {"ok": True, "data": {}}
            raise ZiniaoCliError("Ziniao CLI returned invalid JSON") from None
        if isinstance(response, dict) and response.get("ok") is False:
            self._raise_structured_error(operation, response)
        if completed.returncode:
            raise ZiniaoCliError("Ziniao CLI command failed")
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise ZiniaoCliError("Ziniao CLI request failed")
        return response

    @staticmethod
    def _structured_response(
        completed: subprocess.CompletedProcess[str],
    ) -> object | None:
        values = (
            (completed.stderr, completed.stdout)
            if completed.returncode
            else (completed.stdout,)
        )
        for value in values:
            if not value.strip():
                continue
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _raise_structured_error(operation: str, response: Mapping[str, object]) -> None:
        error = response.get("error", {})
        error_type = (
            str(error.get("type", "")).lower() if isinstance(error, dict) else ""
        )
        error_code = (
            str(error.get("code", "")).lower() if isinstance(error, dict) else ""
        )
        if error_type in {"network", "bridge"} or error_code in {
            "network",
            "bridge_unavailable",
            "bridge_not_ready",
        }:
            raise BridgeUnavailableError("ZClaw Bridge 不可用")
        page_operation = operation in {"page_exec", "page_visit", "session_status"}
        timeout = error_type == "timeout" or error_code == "timeout"
        if error_code == "page_timeout" or timeout:
            if page_operation:
                raise PageTimeoutError("Shopee 页面只读操作超时")
            raise BridgeUnavailableError("ZClaw Bridge 操作超时")
        if error_type == "auth" or error_code in {"login_required", "login_expired"}:
            raise ZiniaoAuthenticationError("Ziniao CLI 认证失败")
        raise ZiniaoCliError("Ziniao CLI request failed")

    @staticmethod
    def _require_store_id(store_id: str) -> None:
        if (
            not isinstance(store_id, str)
            or not store_id.strip()
            or store_id.startswith("-")
        ):
            raise ValueError("store_id must be a non-option value")


class ShopeeSession:
    """只读地复用或启动配置的 Shopee 店铺窗口。"""

    def __init__(
        self,
        settings: Settings,
        client: ZiniaoCliClient | None = None,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.client = client or ZiniaoCliClient(
            settings=settings, timeout_seconds=settings.ziniao_cli_timeout_seconds
        )
        self.sleeper = sleeper
        self.clock = clock

    def ensure_ready(self) -> ShopeeSessionReady:
        store_id = self.settings.ziniao_shopee_store_id.strip()
        if not store_id:
            raise StoreNotFoundError("ZINIAO_SHOPEE_STORE_ID is required")
        store = self._wait_for_store_record(store_id)
        self._validate_store(store)
        reused = self._is_running(store)
        if not reused:
            self._open_store(store_id)
        page = self._wait_for_session_ready(store_id)
        self._assert_logged_in(page)
        return ShopeeSessionReady(
            store_id=store_id,
            store_name=_optional_text(store.get("storeName")) or "",
            reused=reused,
            observed_at=self.clock().astimezone(UTC),
        )

    def _wait_for_session_ready(self, store_id: str) -> Mapping[str, object]:
        last_error: ZiniaoCliError | None = None
        for attempt in range(self.settings.bridge_retries):
            try:
                page = self.client.page_exec(store_id, "session_status")
            except (PageTimeoutError, BridgeUnavailableError) as error:
                last_error = error
                page = None
            else:
                last_error = None
            if self._session_page_ready(page):
                assert isinstance(page, Mapping)
                return page
            if attempt + 1 < self.settings.bridge_retries:
                self.sleeper(self.settings.bridge_retry_delay_seconds)
        if last_error is not None:
            raise last_error from None
        raise PageTimeoutError("Shopee 页面未在配置重试窗口内就绪")

    def _wait_for_store_record(self, store_id: str) -> dict[str, object]:
        last_error: BridgeUnavailableError | None = None
        for attempt in range(self.settings.bridge_retries):
            try:
                stores = self.client.store_list()
            except BridgeUnavailableError as error:
                last_error = error
                stores = []
            else:
                last_error = None
            store = self._configured_store_or_none(stores, store_id)
            if store is not None:
                return store
            if attempt + 1 < self.settings.bridge_retries:
                self.sleeper(self.settings.bridge_retry_delay_seconds)
        if last_error is not None:
            raise BridgeUnavailableError(
                "ZClaw Bridge 未在配置重试窗口内就绪"
            ) from None
        raise StoreNotFoundError("Configured Shopee store was not found")

    def _open_store(self, store_id: str) -> None:
        last_error: BridgeUnavailableError | None = None
        for attempt in range(self.settings.bridge_retries):
            try:
                self.client.store_open(store_id)
                return
            except BridgeUnavailableError as error:
                last_error = error
            if attempt + 1 < self.settings.bridge_retries:
                self.sleeper(self.settings.bridge_retry_delay_seconds)
        if last_error is not None:
            raise BridgeUnavailableError(
                "ZClaw Bridge 未在配置重试窗口内就绪"
            ) from None
        raise BridgeUnavailableError("ZClaw Bridge 未在配置重试窗口内就绪") from None

    @staticmethod
    def _find_configured_store(
        stores: list[dict[str, object]], store_id: str
    ) -> dict[str, object]:
        store = ShopeeSession._configured_store_or_none(stores, store_id)
        if store is not None:
            return store
        raise StoreNotFoundError("Configured Shopee store was not found")

    @staticmethod
    def _configured_store_or_none(
        stores: list[dict[str, object]], store_id: str
    ) -> dict[str, object] | None:
        for store in stores:
            if _optional_text(store.get("storeId")) == store_id:
                return store
        return None

    def _validate_store(self, store: Mapping[str, object]) -> None:
        platform = _optional_text(store.get("platformName")) or ""
        if "shopee" not in platform.lower():
            raise StorePlatformMismatchError("Configured store is not a Shopee store")
        expected_name = self.settings.ziniao_shopee_store_name.strip()
        actual_name = _optional_text(store.get("storeName")) or ""
        if expected_name and actual_name != expected_name:
            raise StoreNameMismatchError("Configured Shopee store name does not match")

    @staticmethod
    def _is_running(store: Mapping[str, object]) -> bool:
        for key in ("running", "isRunning", "isOpen"):
            if key in store:
                value = store[key]
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.lower() in {"true", "running", "open", "active"}
                return False
        status = store.get("status")
        if isinstance(status, str):
            return status.lower() in {"running", "open", "active"}
        # 标准 Bridge 记录无可靠运行字段时，保守地先执行一次 store open。
        return False

    @staticmethod
    def _session_page_ready(page: object) -> bool:
        if not isinstance(page, Mapping):
            return False
        url = page.get("url")
        if not isinstance(url, str) or not url.strip():
            return False
        parsed = urlparse(url)
        return parsed.hostname == "seller.shopee.co.th" and parsed.path.startswith(
            "/portal/marketing/pas/index"
        )

    @staticmethod
    def _assert_logged_in(page: object) -> None:
        if not ShopeeSession._session_page_ready(page):
            raise PageTimeoutError("Shopee 登录状态页面未返回可解析结果")
        assert isinstance(page, Mapping)
        source = " ".join(
            _optional_text(page.get(key)) or "" for key in ("url", "title", "text")
        ).lower()
        if "/login" in source or "log in" in source or "sign in" in source:
            raise ShopeeLoginExpiredError("Shopee 登录已失效")


def parse_shopee_ads_page(
    fixture: Mapping[str, object],
    data_date: date,
    store_id: str | None = None,
    *,
    default_currency: str = "THB",
) -> ShopeeAdsPage:
    """解析脱敏页面契约；不从活动展示名生成稳定活动 ID。"""
    if isinstance(data_date, datetime) or not isinstance(data_date, date):
        raise TypeError("data_date must be a date")
    resolved_store_id = _optional_text(store_id) or _optional_text(
        fixture.get("store_id")
    )
    if not isinstance(resolved_store_id, str) or not resolved_store_id.strip():
        raise ValueError("store_id must not be empty")
    captured_at = _parse_observed_at(fixture.get("captured_at"))
    rows = fixture.get("campaign_rows", [])
    diagnostics: list[str] = []
    if not isinstance(rows, list):
        row_values: list[Mapping[str, object]] = []
        diagnostics.append("malformed_campaign_rows")
    else:
        row_values = [row for row in rows if isinstance(row, Mapping)]
        if len(row_values) != len(rows):
            diagnostics.append("malformed_campaign_rows")
    stable_ids = [
        _stable_campaign_id(
            row,
            resolved_store_id,
            _first_optional_text(row, "campaign_type", "type") or "product_ads",
        )
        for row in row_values
    ]
    ambiguous = {
        campaign_id
        for campaign_id in stable_ids
        if campaign_id is not None and stable_ids.count(campaign_id) > 1
    }
    if ambiguous:
        diagnostics.append("ambiguous_campaign_identity")
    campaigns = tuple(
        snapshot
        for row, stable_id in zip(row_values, stable_ids)
        if stable_id not in ambiguous
        for snapshot in [
            _parse_campaign(
                row, resolved_store_id, captured_at, data_date, default_currency
            )
        ]
        if snapshot is not None
    )
    missing = fixture.get("missing_fields", [])
    missing_fields = (
        [text for value in missing if (text := _optional_text(value)) is not None]
        if isinstance(missing, list)
        else []
    )
    if (
        any(
            isinstance(row, Mapping)
            and _campaign_status(row.get("status")) is CampaignStatus.UNKNOWN
            for row in row_values
        )
        and "status" not in missing_fields
    ):
        missing_fields.append("status")
    for diagnostic in diagnostics:
        if diagnostic not in missing_fields:
            missing_fields.append(diagnostic)
    evidence = fixture.get("stable_id_evidence", {})
    pagination = fixture.get("pagination", {})
    return ShopeeAdsPage(
        data_date=data_date,
        observed_at=captured_at,
        campaigns=campaigns,
        missing_fields=tuple(missing_fields),
        stable_id_evidence=evidence if isinstance(evidence, Mapping) else {},
        pagination=pagination if isinstance(pagination, Mapping) else {},
        raw_contract=fixture,
        diagnostics=tuple(diagnostics),
    )


def validate_shopee_ads_page_url(value: object, data_date: date, page: int) -> None:
    """校验页面返回的 URL 与本次请求的店铺、业务日和页码完全一致。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("page_url must be a non-empty URL")
    if isinstance(data_date, datetime) or not isinstance(data_date, date):
        raise TypeError("data_date must be a date")
    if isinstance(page, bool) or not isinstance(page, int) or page <= 0:
        raise ValueError("page must be a positive integer")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "seller.shopee.co.th"
        or parsed.path != ZiniaoCliClient._SHOPEE_ADS_PATH
        or parsed.fragment
    ):
        raise ValueError("page_url does not match the Shopee Ads page")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key not in query or len(query[key]) != 1 for key in ("from", "to", "page")):
        raise ValueError("page_url is missing required query parameters")
    timezone = ZoneInfo("Asia/Bangkok")
    start = int(datetime.combine(data_date, clock_time.min, timezone).timestamp())
    end = int(datetime.combine(data_date, clock_time.max, timezone).timestamp())
    if query["from"][0] != str(start) or query["to"][0] != str(end):
        raise ValueError("page_url date filter does not match data_date")
    if query["page"][0] != str(page):
        raise ValueError("page_url page does not match requested page")


def _parse_observed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("captured_at must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("captured_at must include timezone information")
    return parsed.astimezone(UTC)


def _parse_campaign(
    row: Mapping[str, object],
    store_id: str,
    observed_at: datetime,
    data_date: date,
    default_currency: str,
) -> CampaignSnapshot | None:
    campaign_type = _first_optional_text(row, "campaign_type", "type") or "product_ads"
    campaign_id = _stable_campaign_id(row, store_id, campaign_type)
    if campaign_id is None:
        return None
    status = _campaign_status(row.get("status"))
    if status is None:
        return None
    name = _first_optional_text(row, "campaign_name", "name") or ""
    currency = normalize_currency(
        _optional_text(row.get("currency")) or default_currency
    )
    return CampaignSnapshot(
        store_id=store_id,
        campaign_id=campaign_id,
        campaign_name=name,
        campaign_type=campaign_type,
        status=status,
        current_budget=_decimal_or_none(
            row.get("daily_budget", row.get("current_budget"))
        ),
        target_roas=_decimal_or_none(row.get("target_roas")),
        currency=currency,
        observed_at=observed_at,
        data_date=data_date,
    )


def _stable_campaign_id(
    row: Mapping[str, object], store_id: str, campaign_type: str
) -> str | None:
    for key in ("page_campaign_id", "campaign_id", "id"):
        value = _optional_text(row.get(key))
        if value:
            return value
    detail_link = (
        _first_optional_text(row, "detail_href", "detail_url", "campaign_url") or ""
    )
    match = re.search(r"(?:campaign[_-]?id=|/campaigns?/)([A-Za-z0-9_-]+)", detail_link)
    if match:
        return match.group(1)
    item_id = _first_optional_text(row, "item_id", "product_id")
    if item_id:
        return f"{store_id}:{campaign_type}:{item_id}"
    return None


def _fallback_campaign_key(row: Mapping[str, object], store_id: str) -> str | None:
    campaign_type = _first_optional_text(row, "campaign_type", "type") or "product_ads"
    has_primary = any(
        _optional_text(row.get(key))
        for key in ("page_campaign_id", "campaign_id", "id")
    )
    detail = (
        _first_optional_text(row, "detail_href", "detail_url", "campaign_url") or ""
    )
    if has_primary or re.search(
        r"(?:campaign[_-]?id=|/campaigns?/)([A-Za-z0-9_-]+)", detail
    ):
        return None
    item_id = _first_optional_text(row, "item_id", "product_id")
    return f"{store_id}:{campaign_type}:{item_id}" if item_id else None


def _campaign_status(value: object) -> CampaignStatus:
    normalized = (_optional_text(value) or "").lower()
    if normalized in {"active", "ongoing", "running"}:
        return CampaignStatus.ACTIVE
    if normalized in {"paused", "pause"}:
        return CampaignStatus.PAUSED
    if normalized in {"ended", "deleted", "end"}:
        return CampaignStatus.ENDED
    return CampaignStatus.UNKNOWN


def _decimal_or_none(value: object) -> Decimal | None:
    raw = _optional_text(value)
    if raw is None or raw in {"-", "—"}:
        return None
    match = _DECIMAL_TEXT_RE.fullmatch(raw.replace("\u00a0", " "))
    if not match or (match.group("prefix") and match.group("suffix")):
        return None
    try:
        parsed = Decimal(match.group("number").replace(",", ""))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


_DECIMAL_TOKEN = r"(?:THB|USD|CNY|฿|\$|¥)"
_DECIMAL_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_DECIMAL_TEXT_RE = re.compile(
    rf"^\s*(?:(?P<prefix>{_DECIMAL_TOKEN})\s*)?(?P<number>{_DECIMAL_NUMBER})"
    rf"(?:\s*(?P<suffix>{_DECIMAL_TOKEN}))?\s*$",
    flags=re.IGNORECASE,
)


def _optional_text(value: object) -> str | None:
    """把页面可空文本规范化；字符串 ``None`` 是普通页面文本。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_optional_text(row: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _optional_text(row.get(key))
        if value is not None:
            return value
    return None

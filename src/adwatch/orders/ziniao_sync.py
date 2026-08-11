from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from adwatch.collectors.ziniao_client import ZiniaoCliClient
from adwatch.integrations.commerce import CommerceRepository
from adwatch.integrations.inventory import InventoryFactRepository
from adwatch.orders.observations import (
    inventory_observations,
    order_observations,
)
from adwatch.orders.repository import OrderRepository
from adwatch.orders.shopee_parser import (
    parse_order_cards,
    parse_product_payload,
)

_PRODUCT_URL = "https://seller.shopee.co.th/portal/product/list/live/all"
_ORDER_URL = "https://seller.shopee.co.th/portal/sale/order"
_ORDER_INDEX_ENDPOINT = "/api/v3/order/search_order_list_index"
_ORDER_CARD_ENDPOINT = "/api/v3/order/get_order_list_card_list"


class OrderSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderSyncResult:
    status: str
    order_count: int
    sku_count: int
    rejected_count: int
    refund_count: int = 0
    logistics_count: int = 0
    inventory_snapshot_count: int = 0
    product_rejected_count: int = 0
    order_rejected_count: int = 0


class ShopeeReadClient(Protocol):
    def product_pages(self) -> Iterable[dict]: ...

    def order_pages(self, *, start: date) -> Iterable[tuple[dict, ...]]: ...


class ZiniaoShopeeReadClient:
    """在紫鸟已登录 Shopee 会话中执行只读 JSON 请求。"""

    def __init__(self, client: ZiniaoCliClient, store_id: str) -> None:
        self.client = client
        self.store_id = store_id

    def product_pages(self) -> Iterable[dict]:
        ready_script = """
        (() => {
          window.__adwatchProductApiUrl = performance
            .getEntriesByType('resource')
            .map(entry => entry.name)
            .reverse()
            .find(url => url.includes('/search_product_list')) || '';
          return Boolean(window.__adwatchProductApiUrl);
        })()
        """
        self.client.navigate_and_exec(
            self.store_id,
            _PRODUCT_URL,
            ready_script,
            expected_url="/portal/product/list/live/all",
            require_nonempty=True,
        )
        cursor = ""
        seen_cursors: set[str] = set()
        received = 0
        while True:
            result = self._browser_fetch(
                """
                const source = window.__adwatchProductApiUrl || performance
                  .getEntriesByType('resource')
                  .map(entry => entry.name)
                  .reverse()
                  .find(value => value.includes('/search_product_list'));
                const url = new URL(source);
                url.searchParams.set('page_size', '48');
                const cursor = __CURSOR__;
                if (cursor) url.searchParams.set('cursor', cursor);
                else url.searchParams.delete('cursor');
                return {url: url.toString(), options: {method: 'GET'}};
                """.replace("__CURSOR__", json.dumps(cursor))
            )
            yield result
            data = result.get("data", {})
            products = data.get("products", []) if isinstance(data, dict) else []
            page_info = data.get("page_info", {}) if isinstance(data, dict) else {}
            received += len(products)
            next_cursor = str(page_info.get("cursor", ""))
            total = int(page_info.get("total", received) or received)
            if (
                not products
                or received >= total
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def order_pages(self, *, start: date) -> Iterable[tuple[dict, ...]]:
        session_script = """
        (() => {
          const source = performance.getEntriesByType('resource')
            .map(entry => entry.name)
            .find(url => url.includes('SPC_CDS='));
          if (!source) return false;
          const parsed = new URL(source);
          window.__adwatchSessionQuery = {
            SPC_CDS: parsed.searchParams.get('SPC_CDS'),
            SPC_CDS_VER: parsed.searchParams.get('SPC_CDS_VER') || '2'
          };
          return Boolean(window.__adwatchSessionQuery.SPC_CDS);
        })()
        """
        self.client.navigate_and_exec(
            self.store_id,
            _ORDER_URL,
            session_script,
            expected_url="/portal/sale/order",
            require_nonempty=True,
        )
        page_number = 1
        sentinel = ""
        while True:
            pagination: dict[str, object] = {
                "page_number": page_number,
                "page_size": 40,
            }
            if page_number > 1:
                pagination.update(
                    {
                        "from_page_number": page_number - 1,
                        "page_sentinel": sentinel,
                    }
                )
            index_payload = {
                "order_list_tab": 100,
                "entity_type": 1,
                "pagination": pagination,
                "filter": {
                    "fulfillment_type": 0,
                    "is_drop_off": 0,
                    "fulfillment_source": 0,
                    "action_filter": 0,
                },
            }
            index_result = self._post(_ORDER_INDEX_ENDPOINT, index_payload)
            data = index_result.get("data", {})
            index_list = data.get("index_list", []) if isinstance(data, dict) else []
            if not index_list:
                break
            cards: list[dict] = []
            for offset in range(0, len(index_list), 5):
                order_params = [
                    {
                        "order_id": item["order_id"],
                        "shop_id": item["shop_id"],
                        "region_id": item["region_id"],
                    }
                    for item in index_list[offset : offset + 5]
                ]
                card_result = self._post(
                    _ORDER_CARD_ENDPOINT,
                    {
                        "order_list_tab": 100,
                        "need_count_down_desc": False,
                        "order_param_list": order_params,
                    },
                )
                card_data = card_result.get("data", {})
                cards.extend(
                    card_data.get("card_list", [])
                    if isinstance(card_data, dict)
                    else []
                )
            yield tuple(cards)
            order_dates = [
                _date_from_order_sn(
                    str(
                        (
                            (
                                card.get("order_card")
                                or card.get("package_level_order_card")
                                or card
                            ).get("card_header")
                            or {}
                        ).get("order_sn", "")
                    )
                )
                for card in cards
            ]
            valid_dates = [item for item in order_dates if item is not None]
            if valid_dates and min(valid_dates) < start:
                break
            page_info = data.get("pagination", {}) if isinstance(data, dict) else {}
            next_sentinel = str(page_info.get("next_page_sentinel", ""))
            total = int(page_info.get("total", 0) or 0)
            if page_number * 40 >= total or not next_sentinel:
                break
            sentinel = next_sentinel
            page_number += 1

    def _post(self, endpoint: str, payload: dict[str, object]) -> dict:
        return self._browser_fetch(
            """
            const source = performance.getEntriesByType('resource')
              .map(entry => entry.name)
              .reverse()
              .find(value => value.includes('SPC_CDS='));
            const parsed = source ? new URL(source) : null;
            const session = window.__adwatchSessionQuery || (parsed ? {
              SPC_CDS: parsed.searchParams.get('SPC_CDS'),
              SPC_CDS_VER: parsed.searchParams.get('SPC_CDS_VER') || '2'
            } : null);
            if (!session || !session.SPC_CDS) {
              throw new Error('Shopee session query not ready');
            }
            window.__adwatchSessionQuery = session;
            const query = new URLSearchParams(session);
            const url = `${location.origin}__ENDPOINT__?${query.toString()}`;
            return {
              url,
              options: {
                method: 'POST',
                headers: {'content-type': 'application/json'},
                body: __PAYLOAD__
              }
            };
            """.replace("__ENDPOINT__", endpoint).replace(
                "__PAYLOAD__", json.dumps(json.dumps(payload, ensure_ascii=False))
            )
        )

    def _browser_fetch(self, request_builder: str) -> dict:
        result_key = f"__adwatchFetch_{uuid.uuid4().hex}"
        key_json = json.dumps(result_key)
        script = f"""
        (() => {{
          window[{key_json}] = null;
          const execute = remaining => {{
            try {{
              const request = (() => {{ {request_builder} }})();
              fetch(request.url, request.options)
                .then(async response => {{
                  const text = await response.text();
                  if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
                  return JSON.parse(text);
                }})
                .then(data => window[{key_json}] = JSON.stringify({{
                  ready: true, data
                }}))
                .catch(error => window[{key_json}] = JSON.stringify({{
                  ready: true, error: String(error)
                }}));
            }} catch (error) {{
              if (remaining > 0) {{
                setTimeout(() => execute(remaining - 1), 500);
              }} else {{
                window[{key_json}] = JSON.stringify({{
                  ready: true, error: String(error)
                }});
              }}
            }}
          }};
          execute(20);
          return 'started';
        }})()
        """
        self.client.page_exec(self.store_id, script)
        wrapped = self.client.page_exec_until(
            self.store_id,
            f"window[{key_json}]",
            ready=lambda value: isinstance(value, dict) and value.get("ready") is True,
            attempts=15,
        )
        if not isinstance(wrapped, dict) or wrapped.get("error"):
            message = (
                wrapped.get("error", "unknown browser fetch error")
                if isinstance(wrapped, dict)
                else "invalid browser fetch response"
            )
            raise OrderSyncError(str(message))
        payload = wrapped.get("data")
        if not isinstance(payload, dict) or int(payload.get("code", 0) or 0) != 0:
            raise OrderSyncError("Shopee read API returned an invalid response")
        return payload


class ShopeeOrderSync:
    def __init__(
        self,
        repository: OrderRepository,
        reader: ShopeeReadClient,
        *,
        store: str,
        clock: Callable[[], datetime] | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> None:
        self.repository = repository
        self.commerce_repository = CommerceRepository(repository.database)
        self.inventory_repository = InventoryFactRepository(
            repository.database
        )
        self.reader = reader
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timezone = ZoneInfo(timezone)

    def sync(self, *, start: date, end: date) -> OrderSyncResult:
        if start > end:
            raise ValueError("start date must not be after end date")
        run_id = str(uuid.uuid4())
        started_at = self._now()
        self.repository.start_sync_run(
            run_id=run_id,
            platform="shopee",
            store=self.store,
            started_at=started_at,
            start_date=start,
            end_date=end,
        )
        sku_count = 0
        order_count = 0
        refund_count = 0
        logistics_count = 0
        inventory_snapshot_count = 0
        product_rejected: list[str] = []
        order_rejected: list[str] = []
        try:
            for payload in self.reader.product_pages():
                parsed = parse_product_payload(
                    payload,
                    store=self.store,
                    observed_at=started_at,
                )
                sku_count += self.repository.upsert_skus(parsed.skus)
                inventory_snapshot_count += (
                    self.inventory_repository.upsert_snapshots(
                        inventory_observations(
                            parsed.skus,
                            observed_at=started_at,
                        )
                    )
                )
                product_rejected.extend(parsed.rejected)
            sku_map = self.repository.sku_map(
                platform="shopee",
                store=self.store,
            )
            for cards in self.reader.order_pages(start=start):
                parsed = parse_order_cards(
                    cards,
                    store=self.store,
                    observed_at=started_at,
                    sku_map=sku_map,
                    start=start,
                    end=end,
                )
                order_count += self.repository.upsert_orders(parsed.orders)
                refunds, logistics = order_observations(
                    parsed.orders,
                    observed_at=started_at,
                )
                refund_count += (
                    self.commerce_repository.upsert_refund_observations(
                        refunds
                    )
                )
                logistics_count += (
                    self.commerce_repository.upsert_logistics_observations(
                        logistics
                    )
                )
                order_rejected.extend(parsed.rejected)
        except Exception as error:
            self.repository.finish_sync_run(
                run_id=run_id,
                finished_at=self._now(),
                status="failed",
                order_count=order_count,
                sku_count=sku_count,
                rejected_count=len(product_rejected) + len(order_rejected),
                product_rejected_count=len(product_rejected),
                order_rejected_count=len(order_rejected),
                refund_count=refund_count,
                logistics_count=logistics_count,
                inventory_snapshot_count=inventory_snapshot_count,
                error_message=f"{type(error).__name__}: {error}"[:500],
            )
            raise OrderSyncError(str(error)) from error
        status = "partial" if order_rejected else "success"
        self.repository.finish_sync_run(
            run_id=run_id,
            finished_at=self._now(),
            status=status,
            order_count=order_count,
            sku_count=sku_count,
            rejected_count=len(product_rejected) + len(order_rejected),
            product_rejected_count=len(product_rejected),
            order_rejected_count=len(order_rejected),
            refund_count=refund_count,
            logistics_count=logistics_count,
            inventory_snapshot_count=inventory_snapshot_count,
            error_message=(
                "; ".join(product_rejected + order_rejected)[:500]
                if product_rejected or order_rejected
                else None
            ),
        )
        return OrderSyncResult(
            status,
            order_count,
            sku_count,
            len(product_rejected) + len(order_rejected),
            refund_count,
            logistics_count,
            inventory_snapshot_count,
            len(product_rejected),
            len(order_rejected),
        )

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None:
            raise ValueError("order sync clock must be timezone-aware")
        return current.astimezone(self.timezone)


def _date_from_order_sn(order_sn: str) -> date | None:
    if len(order_sn) < 6 or not order_sn[:6].isdigit():
        return None
    try:
        value = order_sn[:6]
        return date(2000 + int(value[:2]), int(value[2:4]), int(value[4:6]))
    except ValueError:
        return None

"""Shopee Ads 的只读日采集、验证与原子落库。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from adwatch.config import Settings
from adwatch.optimizer.models import (
    AdMetric,
    CampaignSnapshot,
    DataQuality,
    DataQualityStatus,
    MetricGrain,
    normalize_currency,
)
from adwatch.optimizer.repository import OptimizerRepository
from adwatch.optimizer.ziniao import (
    PageTimeoutError,
    ZiniaoCliClient,
    normalize_campaign_name,
    parse_shopee_ads_page,
    validate_shopee_ads_page_url,
)


class ShopeeAdsPageReader(Protocol):
    """固定业务日、指定页码的只读页面契约。"""

    def read_page(
        self, store_id: str, data_date: date, page: int
    ) -> Mapping[str, object]: ...


class ZiniaoShopeeAdsReader:
    """将已注入客户端限制在固定页面和白名单只读脚本内。"""

    def __init__(
        self,
        client: ZiniaoCliClient,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.sleeper = sleeper

    def read_page(
        self, store_id: str, data_date: date, page: int
    ) -> Mapping[str, object]:
        self.client.visit_shopee_ads_page(store_id, data_date, page)
        if page == 1:
            value = self._wait_for_page(store_id, data_date, page, page)
            if value.get("has_shop_gmv_max") is True:
                self.client.page_shop_gmv_max(store_id)
                shop = self._wait_for_shop_gmv_max(store_id)
                self.client.page_individual_ads(store_id)
                restored = self._wait_for_page(store_id, data_date, page, page)
                return _merge_shop_gmv_max(restored, shop)
            return value
        value: Mapping[str, object] | None = self._wait_for_page(
            store_id, data_date, page, 1
        )
        for expected_page in range(2, page + 1):
            self.client.page_next(store_id)
            value = self._wait_for_page(
                store_id, data_date, page, expected_page
            )
        assert value is not None
        return value

    def _wait_for_page(
        self,
        store_id: str,
        data_date: date,
        requested_page: int,
        expected_page: int,
    ) -> Mapping[str, object]:
        for attempt in range(self.client.settings.page_ready_retries):
            value = self.client.page_exec(store_id, "shopee_ads_contract_page")
            if not isinstance(value, Mapping):
                raise TypeError("Shopee Ads 页面契约不是对象")
            validate_shopee_ads_page_url(
                value.get("page_url"), data_date, requested_page
            )
            if _page_contract_ready(value, expected_page):
                return value
            if attempt + 1 < self.client.settings.page_ready_retries:
                self.sleeper(
                    self.client.settings.page_ready_retry_delay_seconds
                )
        raise PageTimeoutError("Shopee Ads 页面数据未在配置重试窗口内就绪")

    def _wait_for_shop_gmv_max(self, store_id: str) -> Mapping[str, object]:
        for attempt in range(self.client.settings.page_ready_retries):
            value = self.client.page_exec(
                store_id, "shopee_shop_gmv_max_contract"
            )
            if not isinstance(value, Mapping):
                raise TypeError("Shop GMV Max 页面契约不是对象")
            if isinstance(value.get("campaign_row"), Mapping):
                return value
            if attempt + 1 < self.client.settings.page_ready_retries:
                self.sleeper(
                    self.client.settings.page_ready_retry_delay_seconds
                )
        raise PageTimeoutError("Shop GMV Max 数据未在配置重试窗口内就绪")


@dataclass(frozen=True)
class CollectionResult:
    metrics: tuple[AdMetric, ...]
    snapshots: tuple[CampaignSnapshot, ...]
    quality: DataQuality
    diagnostics: tuple[str, ...] = ()
    pages_read: int = 0
    complete: bool = True
    persisted: bool = False


class ShopeeAdCollector:
    """只读采集 Shopee Ads：完整验证完成后才替换一个业务日。"""

    source = "ziniao-cli/read-only"

    def __init__(
        self,
        settings: Settings,
        reader: ShopeeAdsPageReader | ZiniaoCliClient,
        *,
        repository: OptimizerRepository | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.reader: ShopeeAdsPageReader = (
            ZiniaoShopeeAdsReader(reader, sleeper=sleeper)
            if isinstance(reader, ZiniaoCliClient)
            else reader
        )
        self.repository = repository
        self.sleeper = sleeper

    def collect(
        self, data_date: date, *, claim_token: str | None = None
    ) -> CollectionResult:
        if isinstance(data_date, datetime) or not isinstance(data_date, date):
            raise TypeError("data_date must be a date")
        store_id = self.settings.ziniao_shopee_store_id.strip()
        if not store_id:
            raise ValueError("ZINIAO_SHOPEE_STORE_ID must not be empty")

        try:
            pages, pagination_diagnostics, read_complete = self._read_all_pages(
                store_id, data_date
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            if self.repository is not None:
                self.repository.record_collection_failure(
                    store_id,
                    data_date,
                    note="page_read_failed",
                    claim_token=claim_token,
                )
            raise
        result = self._parse_pages(
            store_id, data_date, pages, pagination_diagnostics, read_complete
        )
        if (
            self.repository is not None
            and result.complete
            and not (
                not result.metrics
                and self.repository.has_successful_collection(store_id, data_date)
            )
        ):
            if result.metrics:
                self.repository.replace_store_day(
                    store_id,
                    data_date,
                    result.metrics,
                    result.snapshots,
                    "success",
                    note=";".join(result.diagnostics),
                    claim_token=claim_token,
                )
            else:
                self.repository.record_collection_day(
                    store_id,
                    data_date,
                    "no_data",
                    note=";".join(result.diagnostics),
                    claim_token=claim_token,
                )
            return replace(result, persisted=True)
        if self.repository is not None and not result.complete:
            if not result.metrics and any(
                page.get("fixture_kind") == "real_observation" for page in pages
            ):
                self.repository.record_collection_day(
                    store_id,
                    data_date,
                    "no_data",
                    note=";".join(result.diagnostics),
                    claim_token=claim_token,
                )
            else:
                self.repository.record_collection_failure(
                    store_id,
                    data_date,
                    note=";".join(result.diagnostics),
                    claim_token=claim_token,
                )
        return result

    def _read_all_pages(
        self, store_id: str, data_date: date
    ) -> tuple[tuple[Mapping[str, object], ...], list[str], bool]:
        pages: list[Mapping[str, object]] = []
        diagnostics: list[str] = []
        fingerprints: set[str] = set()
        requested_page = 1
        complete = True
        while requested_page <= self.settings.shopee_ads_max_pages:
            raw = self._read_page_with_retry(store_id, data_date, requested_page)
            if "page_url" in raw:
                validate_shopee_ads_page_url(
                    raw.get("page_url"), data_date, requested_page
                )
            fingerprint = _fingerprint(raw)
            if fingerprint in fingerprints:
                diagnostics.append("duplicate_page")
                complete = False
                break
            fingerprints.add(fingerprint)
            pages.append(raw)
            pagination = raw.get("pagination")
            if not isinstance(pagination, Mapping):
                diagnostics.append("pagination:malformed")
                complete = False
                break
            current_page = _positive_int_or_none(pagination.get("page"))
            total_pages = _positive_int_or_none(pagination.get("total_pages"))
            has_next_value = pagination.get("has_next")
            if (
                current_page is None
                or total_pages is None
                or not isinstance(has_next_value, bool)
            ):
                diagnostics.append("pagination:malformed")
                complete = False
                break
            has_next = has_next_value
            if current_page is not None and current_page != requested_page:
                diagnostics.append("pagination:unexpected_page")
                complete = False
                break
            if (
                total_pages is not None
                and current_page is not None
                and current_page > total_pages
            ):
                diagnostics.append("pagination:invalid_total")
                complete = False
                break
            if (
                total_pages is not None
                and current_page is not None
                and has_next
                and current_page >= total_pages
            ):
                diagnostics.append("pagination:contradictory_next")
                complete = False
                break
            if (
                total_pages is not None
                and current_page is not None
                and not has_next
                and current_page < total_pages
            ):
                diagnostics.append("pagination:contradictory_next")
                complete = False
                break
            if not has_next:
                break
            requested_page += 1
        else:
            diagnostics.append("pagination:max_pages")
            complete = False
        return tuple(pages), diagnostics, complete

    def _read_page_with_retry(
        self, store_id: str, data_date: date, page: int
    ) -> Mapping[str, object]:
        error: Exception | None = None
        for attempt in range(self.settings.bridge_retries):
            try:
                raw = self.reader.read_page(store_id, data_date, page)
                if not isinstance(raw, Mapping):
                    raise TypeError("Shopee Ads 页面契约不是对象")
                return raw
            except (OSError, RuntimeError, TypeError, ValueError) as caught:
                error = caught
                if attempt + 1 < self.settings.bridge_retries:
                    self.sleeper(self.settings.bridge_retry_delay_seconds)
        assert error is not None
        raise error

    def _parse_pages(
        self,
        store_id: str,
        data_date: date,
        pages: tuple[Mapping[str, object], ...],
        pagination_diagnostics: list[str],
        read_complete: bool,
    ) -> CollectionResult:
        diagnostics = list(pagination_diagnostics)
        missing_fields: list[str] = []
        metrics: list[AdMetric] = []
        snapshots: list[CampaignSnapshot] = []
        metric_keys: set[tuple[str, str]] = set()
        seen_snapshots: set[tuple[str, datetime]] = set()
        overview: AdMetric | None = None
        campaign_metrics: list[AdMetric] = []
        complete = read_complete

        for page_index, raw in enumerate(pages, start=1):
            try:
                parsed = parse_shopee_ads_page(
                    raw,
                    data_date,
                    store_id,
                    default_currency=self.settings.shopee_currency,
                )
            except (TypeError, ValueError):
                diagnostics.append("page_contract:invalid")
                missing_fields.append("page_contract")
                complete = False
                continue
            _extend_unique(missing_fields, parsed.missing_fields)
            _extend_unique(diagnostics, parsed.diagnostics)
            accepted_campaign_ids = {
                snapshot.campaign_id for snapshot in parsed.campaigns
            }
            blocked_ids = _ambiguous_campaign_ids(raw, store_id)
            if blocked_ids:
                _extend_unique(diagnostics, ["ambiguous_campaign_identity"])

            for snapshot in parsed.campaigns:
                if snapshot.campaign_id in blocked_ids:
                    continue
                key = (snapshot.campaign_id, snapshot.observed_at)
                if key not in seen_snapshots:
                    seen_snapshots.add(key)
                    snapshots.append(snapshot)

            if page_index == 1:
                row = raw.get("overview")
                if isinstance(row, Mapping):
                    metric = self._metric_from_row(
                        row,
                        store_id,
                        "__OVERVIEW__",
                        "",
                        "__ALL__",
                        MetricGrain.OVERVIEW,
                        data_date,
                        parsed.observed_at,
                        missing_fields,
                        diagnostics,
                    )
                    if metric is not None:
                        overview = metric
                        self._append_metric(metrics, metric_keys, metric, diagnostics)
                else:
                    missing_fields.append("overview")

            campaign_rows = raw.get("campaign_rows", [])
            if not isinstance(campaign_rows, list):
                missing_fields.append("campaign_rows")
                campaign_rows = []
            for row in campaign_rows:
                if not isinstance(row, Mapping):
                    missing_fields.append("campaign_row")
                    continue
                campaign_id = _stable_campaign_id(row, store_id)
                if (
                    campaign_id is None
                    or campaign_id in blocked_ids
                    or campaign_id not in accepted_campaign_ids
                ):
                    if campaign_id is None:
                        missing_fields.append("campaign_id")
                    continue
                metric = self._metric_from_row(
                    row,
                    store_id,
                    campaign_id,
                    normalize_campaign_name(
                        _text(row.get("campaign_name")) or _text(row.get("name"))
                    ),
                    "__ALL__",
                    MetricGrain.CAMPAIGN,
                    data_date,
                    parsed.observed_at,
                    missing_fields,
                    diagnostics,
                )
                if metric is not None:
                    campaign_metrics.append(metric)
                    self._append_metric(metrics, metric_keys, metric, diagnostics)

            product_rows = raw.get("product_rows", [])
            if not isinstance(product_rows, list):
                missing_fields.append("product_rows")
                product_rows = []
            for row in product_rows:
                if not isinstance(row, Mapping):
                    missing_fields.append("product_row")
                    continue
                campaign_id = _stable_campaign_id(row, store_id)
                item_id = _text(row.get("item_id")) or _text(row.get("product_id"))
                if campaign_id is None or campaign_id in blocked_ids or not item_id:
                    missing_fields.append(
                        "campaign_id" if campaign_id is None else "item_id"
                    )
                    continue
                metric = self._metric_from_row(
                    row,
                    store_id,
                    campaign_id,
                    normalize_campaign_name(
                        _text(row.get("campaign_name")) or _text(row.get("name"))
                    ),
                    item_id,
                    MetricGrain.ITEM,
                    data_date,
                    parsed.observed_at,
                    missing_fields,
                    diagnostics,
                )
                if metric is not None:
                    self._append_metric(metrics, metric_keys, metric, diagnostics)

        reconciled = self._reconcile(overview, campaign_metrics, diagnostics)
        _extend_unique(missing_fields, _diagnostic_missing_fields(diagnostics))
        has_problem = bool(
            missing_fields or diagnostics or not reconciled or not complete
        )
        status = (
            DataQualityStatus.DEGRADED if has_problem else DataQualityStatus.HEALTHY
        )
        assessed_at = max(
            (snapshot.observed_at for snapshot in snapshots), default=datetime.now(UTC)
        )
        quality = DataQuality(
            status=status,
            assessed_at=assessed_at,
            allow_aggressive_scale=status is DataQualityStatus.HEALTHY,
            missing_fields=tuple(missing_fields),
            note=";".join(diagnostics),
        )
        return CollectionResult(
            tuple(metrics),
            tuple(snapshots),
            quality,
            tuple(diagnostics),
            len(pages),
            complete,
        )

    def _metric_from_row(
        self,
        row: Mapping[str, object],
        store_id: str,
        campaign_id: str,
        campaign_name: str,
        item_id: str,
        grain: MetricGrain,
        data_date: date,
        collected_at: datetime,
        missing_fields: list[str],
        diagnostics: list[str],
    ) -> AdMetric | None:
        try:
            currency = _normalize_currency(
                _text(row.get("currency"))
                or _currency_from_values(row)
                or self.settings.shopee_currency
            )
            if not currency:
                raise _RowFieldError("currency")
            metric = AdMetric(
                store_id=store_id,
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                item_id=item_id,
                data_date=data_date,
                currency=currency.upper(),
                spend=_money(_first(row, "spend", "expense", "cost"), "spend"),
                gmv=_money(_first(row, "gmv", "sales", "attributed_gmv"), "gmv"),
                orders=_orders(_first(row, "orders", "conversions", "items_sold")),
                collected_at=collected_at,
                source=self.source,
                grain=grain,
            )
        except _RowFieldError as error:
            missing_fields.append(f"metric.{error.field}")
            return None
        self._diagnose_derived_value(
            "reported_roas",
            _first(row, "roas", "reported_roas"),
            metric.roas,
            diagnostics,
        )
        self._diagnose_derived_value(
            "reported_cpa",
            _first(row, "cpa", "reported_cpa", "cost_per_conversion"),
            metric.cpa,
            diagnostics,
        )
        return metric

    def _diagnose_derived_value(
        self,
        name: str,
        value: object,
        calculated: Decimal | None,
        diagnostics: list[str],
    ) -> None:
        if value is None or _is_numeric_missing(value):
            return
        try:
            reported = _number(value, name)
        except _RowFieldError:
            diagnostics.append(f"{name}:invalid")
            return
        if calculated is None:
            if reported != 0:
                diagnostics.append(f"{name}:mismatch")
            return
        if abs(calculated - reported) > self.settings.reconciliation_tolerance:
            diagnostics.append(f"{name}:mismatch")

    @staticmethod
    def _append_metric(
        metrics: list[AdMetric],
        keys: set[tuple[str, str]],
        metric: AdMetric,
        diagnostics: list[str],
    ) -> None:
        key = (metric.campaign_id, metric.item_id)
        if key in keys:
            diagnostics.append("duplicate_metric_row")
            return
        keys.add(key)
        metrics.append(metric)

    def _reconcile(
        self,
        overview: AdMetric | None,
        campaign_metrics: list[AdMetric],
        diagnostics: list[str],
    ) -> bool:
        if overview is None or not campaign_metrics:
            diagnostics.append("reconciliation:unavailable")
            return False
        if any(metric.currency != overview.currency for metric in campaign_metrics):
            diagnostics.append("reconciliation:unavailable")
            return False
        totals = (
            sum((metric.spend for metric in campaign_metrics), Decimal(0)),
            sum((metric.gmv for metric in campaign_metrics), Decimal(0)),
        )
        comparable = (overview.spend, overview.gmv)
        fields = ("spend", "gmv")
        valid = True
        for field, actual, expected in zip(fields, totals, comparable, strict=True):
            difference = abs(actual - expected)
            failed = difference > self.settings.reconciliation_tolerance
            if failed:
                diagnostics.append(f"reconciliation:{field}")
                valid = False
        return valid


class _RowFieldError(ValueError):
    def __init__(self, field: str) -> None:
        self.field = field


def _page_contract_ready(
    value: Mapping[str, object], expected_page: int
) -> bool:
    pagination = value.get("pagination")
    rows = value.get("campaign_rows")
    if not isinstance(pagination, Mapping) or not isinstance(rows, list):
        return False
    page = _positive_int_or_none(pagination.get("page"))
    total_pages = _positive_int_or_none(pagination.get("total_pages"))
    has_next = pagination.get("has_next")
    ads_list_view = value.get("ads_list_view")
    return (
        page == expected_page
        and total_pages is not None
        and isinstance(has_next, bool)
        and ads_list_view in {None, "individual_ads"}
        and (expected_page != 1 or isinstance(value.get("overview"), Mapping))
        and (bool(rows) or expected_page == 1)
    )


def _merge_shop_gmv_max(
    standard: Mapping[str, object], shop: Mapping[str, object]
) -> Mapping[str, object]:
    merged = dict(standard)
    rows = standard.get("campaign_rows", [])
    merged_rows = list(rows) if isinstance(rows, list) else []
    campaign_row = shop.get("campaign_row")
    if isinstance(campaign_row, Mapping):
        merged_rows.append(dict(campaign_row))
    merged["campaign_rows"] = merged_rows
    missing = standard.get("missing_fields", [])
    merged_missing = list(missing) if isinstance(missing, list) else []
    shop_missing = shop.get("missing_fields", [])
    if isinstance(shop_missing, list):
        _extend_unique(
            merged_missing,
            tuple(str(value) for value in shop_missing if str(value).strip()),
        )
    merged["missing_fields"] = merged_missing
    return merged


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first(row: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _number(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool) or _is_numeric_missing(value):
        raise _RowFieldError(field)
    text = str(value).strip().replace("\u00a0", " ")
    match = _DECIMAL_TEXT_RE.fullmatch(text)
    if match is None or (match.group("prefix") and match.group("suffix")):
        raise _RowFieldError(field) from None
    try:
        result = Decimal(match.group("number").replace(",", ""))
    except InvalidOperation:
        raise _RowFieldError(field) from None
    if not result.is_finite() or result < 0:
        raise _RowFieldError(field)
    return result


def _money(value: object, field: str) -> Decimal:
    return _number(value, field)


def _orders(value: object) -> int:
    result = _number(value, "orders")
    if result != result.to_integral_value():
        raise _RowFieldError("orders")
    return int(result)


def _currency_from_values(row: Mapping[str, object]) -> str | None:
    values = " ".join(str(value) for value in row.values())
    if "฿" in values or re.search(r"\bTHB\b", values, flags=re.IGNORECASE):
        return "THB"
    if "$" in values or re.search(r"\bUSD\b", values, flags=re.IGNORECASE):
        return "USD"
    if "¥" in values or re.search(r"\bCNY\b", values, flags=re.IGNORECASE):
        return "CNY"
    return None


def _normalize_currency(value: str) -> str:
    try:
        return normalize_currency(value)
    except (TypeError, ValueError):
        raise _RowFieldError("currency")


def _stable_campaign_id(row: Mapping[str, object], store_id: str) -> str | None:
    for key in ("page_campaign_id", "campaign_id", "id"):
        if value := _text(row.get(key)):
            return value
    link = _text(row.get("detail_href")) or _text(row.get("detail_url")) or ""
    match = re.search(r"(?:campaign[_-]?id=|/campaigns?/)([A-Za-z0-9_-]+)", link)
    if match:
        return match.group(1)
    item_id = _text(row.get("item_id")) or _text(row.get("product_id"))
    campaign_type = (
        _text(row.get("campaign_type")) or _text(row.get("type")) or "product_ads"
    )
    return f"{store_id}:{campaign_type}:{item_id}" if item_id else None


def _ambiguous_campaign_ids(raw: Mapping[str, object], store_id: str) -> set[str]:
    rows = raw.get("campaign_rows", [])
    if not isinstance(rows, list):
        return set()
    values = [
        campaign_id
        for row in rows
        if isinstance(row, Mapping)
        if (campaign_id := _stable_campaign_id(row, store_id)) is not None
    ]
    return {campaign_id for campaign_id in values if values.count(campaign_id) > 1}


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


_DECIMAL_TOKEN = r"(?:THB|USD|CNY|฿|\$|¥)"
_DECIMAL_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_DECIMAL_TEXT_RE = re.compile(
    rf"^\s*(?:(?P<prefix>{_DECIMAL_TOKEN})\s*)?(?P<number>{_DECIMAL_NUMBER})"
    rf"(?:\s*(?P<suffix>{_DECIMAL_TOKEN}))?\s*$",
    flags=re.IGNORECASE,
)


def _is_numeric_missing(value: object) -> bool:
    return isinstance(value, str) and value.strip() in {"", "-", "—"}


def _fingerprint(value: Mapping[str, object]) -> str:
    """忽略捕获时间和页码，识别服务端反复返回的同一逻辑页。"""
    return json.dumps(
        {
            "overview": _fingerprint_row(value.get("overview")),
            "campaign_rows": sorted(
                _fingerprint_row(row)
                for row in value.get("campaign_rows", [])
                if isinstance(row, Mapping)
            ),
            "product_rows": sorted(
                _fingerprint_row(row)
                for row in value.get("product_rows", [])
                if isinstance(row, Mapping)
            ),
        },
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint_row(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        return ()
    keys = (
        "page_campaign_id",
        "campaign_id",
        "id",
        "detail_href",
        "detail_url",
        "item_id",
        "product_id",
        "currency",
        "spend",
        "expense",
        "cost",
        "gmv",
        "sales",
        "attributed_gmv",
        "orders",
        "conversions",
        "items_sold",
    )
    return tuple((key, str(value[key])) for key in keys if key in value)


def _extend_unique(values: list[str], additions: list[str] | tuple[str, ...]) -> None:
    for item in additions:
        if item not in values:
            values.append(item)


def _diagnostic_missing_fields(diagnostics: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    for diagnostic in diagnostics:
        if diagnostic in {"duplicate_page", "duplicate_metric_row"}:
            values.append(diagnostic)
        elif diagnostic.startswith("pagination:"):
            values.append("pagination")
        elif diagnostic.startswith("reconciliation:"):
            values.append("reconciliation")
        elif diagnostic.startswith("reported_roas:"):
            values.append("reported_roas")
        elif diagnostic.startswith("reported_cpa:"):
            values.append("reported_cpa")
        elif diagnostic == "ambiguous_campaign_identity":
            values.append("campaign_id")
    return tuple(values)

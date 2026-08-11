from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from adwatch.orders.models import ParseResult, PlatformOrderLine, PlatformSku

_ITEM = re.compile(
    r"(?P<name>.+?)\s+Parent SKU:.*?Item ID:\s*(?P<item>\d+)"
    r"(?P<body>.*?)(?=(?:\S.+?\s+Parent SKU:.*?Item ID:)|\Z)",
    re.DOTALL,
)
_MODEL = re.compile(
    r"(?P<variation>[^\n]+?)\s+SKU:\s*(?P<sku>.+?)\s+"
    r"Model ID:\s*(?P<model>\d+)\s+(?P<stock>Sold out|\d+)\s+Sales",
)


def parse_product_page(
    text: str,
    *,
    store: str,
    observed_at: datetime,
) -> ParseResult:
    normalized = " ".join(text.split())
    skus: list[PlatformSku] = []
    rejected: list[str] = []
    for item in _ITEM.finditer(normalized):
        body = re.sub(r"^\s*AMS Commission\s*", "", item.group("body"))
        models = list(_MODEL.finditer(body))
        if not models:
            rejected.append(f"item {item.group('item')} has no parseable models")
            continue
        for model in models:
            skus.append(
                PlatformSku(
                    platform="shopee",
                    store=store,
                    item_id=item.group("item"),
                    model_id=model.group("model"),
                    seller_sku=model.group("sku").strip(),
                    variation_name=model.group("variation").strip(),
                    product_name=item.group("name").strip(),
                    inventory_units=(
                        0
                        if model.group("stock") == "Sold out"
                        else int(model.group("stock"))
                    ),
                    observed_at=observed_at,
                )
            )
    return ParseResult(skus=tuple(skus), rejected=tuple(rejected))


def parse_product_payload(
    payload: dict[str, Any],
    *,
    store: str,
    observed_at: datetime,
) -> ParseResult:
    """把 Shopee 商品列表 JSON 转成稳定的 Item/Model/SKU 映射。"""
    data = payload.get("data", payload)
    products = data.get("products", []) if isinstance(data, dict) else []
    skus: list[PlatformSku] = []
    rejected: list[str] = []
    for product in products:
        item_id = str(product.get("id", "")).strip()
        product_name = str(product.get("name", "")).strip()
        models = product.get("model_list") or []
        if not item_id or not models:
            rejected.append(f"product {item_id or '<unknown>'} has no models")
            continue
        for model in models:
            model_id = str(model.get("id", "")).strip()
            seller_sku = str(model.get("sku", "")).strip()
            if not model_id or not seller_sku:
                rejected.append(
                    f"product {item_id} model {model_id or '<unknown>'} "
                    "has no Seller SKU"
                )
                continue
            stock_detail = model.get("stock_detail") or {}
            try:
                inventory_units = int(
                    stock_detail.get("total_available_stock", 0)
                )
            except (TypeError, ValueError):
                rejected.append(
                    f"product {item_id} model {model_id} has invalid stock"
                )
                continue
            if inventory_units < 0:
                rejected.append(
                    f"product {item_id} model {model_id} has invalid stock"
                )
                continue
            skus.append(
                PlatformSku(
                    platform="shopee",
                    store=store,
                    item_id=item_id,
                    model_id=model_id,
                    seller_sku=seller_sku,
                    variation_name=str(model.get("name", "")).strip(),
                    product_name=product_name,
                    inventory_units=inventory_units,
                    observed_at=observed_at,
                )
            )
    return ParseResult(skus=tuple(skus), rejected=tuple(rejected))


def parse_order_cards(
    cards: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    store: str,
    observed_at: datetime,
    sku_map: dict[tuple[str, str], tuple[str, str, str]],
    start: date,
    end: date,
) -> ParseResult:
    """解析订单卡 JSON，并通过 Item/Model 关联 Seller SKU。"""
    orders: list[PlatformOrderLine] = []
    rejected: list[str] = []
    normalized_cards = tuple(
        card
        for wrapper in cards
        for card in _normalize_order_cards(wrapper)
    )
    for card in normalized_cards:
        header = card.get("card_header") or {}
        order_sn = str(header.get("order_sn", "")).strip()
        ordered_on = _order_date(order_sn)
        if ordered_on is None:
            rejected.append(f"order {order_sn or '<unknown>'} has invalid date")
            continue
        if ordered_on < start or ordered_on > end:
            continue

        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        item_group = card.get("item_info_group") or {}
        for group in item_group.get("item_info_list") or []:
            for item in group.get("item_list") or []:
                ext = item.get("inner_item_ext_info") or {}
                item_id = str(ext.get("item_id", "")).strip()
                model_id = str(ext.get("model_id", "")).strip()
                if not item_id or not model_id:
                    rejected.append(
                        f"order {order_sn} has item without Item/Model ID"
                    )
                    continue
                key = (item_id, model_id)
                row = grouped.setdefault(
                    key,
                    {
                        "quantity": 0,
                        "product_name": str(item.get("name", "")).strip(),
                        "variation_name": _variation_name(
                            str(item.get("description", ""))
                        ),
                    },
                )
                try:
                    row["quantity"] += int(item.get("amount", 1))
                except (TypeError, ValueError):
                    rejected.append(f"order {order_sn} has invalid quantity")

        resolved: list[dict[str, Any]] = []
        for key, item in grouped.items():
            mapping = sku_map.get(key)
            if mapping is None:
                rejected.append(
                    f"order {order_sn}: unmapped item/model {key[0]}/{key[1]}"
                )
                continue
            if item["quantity"] <= 0:
                rejected.append(
                    f"order {order_sn}: invalid quantity for {key[0]}/{key[1]}"
                )
                continue
            seller_sku, variation_name, product_name = mapping
            resolved.append(
                {
                    "item_id": key[0],
                    "model_id": key[1],
                    "seller_sku": seller_sku,
                    "variation_name": variation_name or item["variation_name"],
                    "product_name": product_name or item["product_name"],
                    "quantity": item["quantity"],
                }
            )
        if not resolved:
            continue

        payment = card.get("payment_info") or {}
        total_paid = _shopee_money(payment.get("total_price", 0))
        allocations = _allocate_paid(total_paid, resolved)
        status_info = card.get("status_info") or {}
        order_status = str(status_info.get("status", "")).strip()
        status_lower = order_status.lower()
        refund_status = (
            order_status
            if "refund" in status_lower or "return" in status_lower
            else ""
        )
        order_ext = card.get("order_ext_info") or {}
        package_list = card.get("package_ext_info_list") or []
        logistics_status = str(
            (package_list[0].get("fulfilment_status") if package_list else None)
            or order_ext.get("logistics_status", "")
        )
        for item, buyer_paid in zip(resolved, allocations, strict=True):
            orders.append(
                PlatformOrderLine(
                    platform="shopee",
                    store=store,
                    order_id=order_sn,
                    item_id=item["item_id"],
                    model_id=item["model_id"],
                    seller_sku=item["seller_sku"],
                    variation_name=item["variation_name"],
                    product_name=item["product_name"],
                    quantity=item["quantity"],
                    buyer_paid=buyer_paid,
                    currency="THB",
                    order_status=order_status,
                    logistics_status=logistics_status,
                    refund_status=refund_status,
                    ordered_at=ordered_on,
                    source_updated_at=observed_at,
                )
            )
    return ParseResult(orders=tuple(orders), rejected=tuple(rejected))


def _normalize_order_cards(wrapper: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    standard = wrapper.get("order_card")
    if isinstance(standard, dict):
        return (standard,)
    package_level = wrapper.get("package_level_order_card")
    if not isinstance(package_level, dict):
        return (wrapper,)
    header = package_level.get("card_header") or {}
    order_ext = package_level.get("order_ext_info") or {}
    packages = package_level.get("package_list") or []
    return tuple(
        {
            "card_header": header,
            "order_ext_info": order_ext,
            "payment_info": package.get("payment_info") or {},
            "status_info": package.get("status_info") or {},
            "item_info_group": package.get("item_info_group") or {},
            "package_ext_info_list": [
                package.get("package_ext_info") or {}
            ],
        }
        for package in packages
        if isinstance(package, dict)
    )


def _order_date(order_sn: str) -> date | None:
    matched = re.match(r"^(?P<value>\d{6})", order_sn)
    if matched is None:
        return None
    try:
        value = matched.group("value")
        return date(2000 + int(value[:2]), int(value[2:4]), int(value[4:6]))
    except ValueError:
        return None


def _variation_name(description: str) -> str:
    return re.sub(r"^\s*Variation:\s*", "", description).strip()


def _shopee_money(value: object) -> Decimal:
    try:
        return (Decimal(str(value)) / Decimal(100000)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except Exception:  # noqa: BLE001 - 外部 JSON 金额非法时按零并保留订单
        return Decimal("0.00")


def _allocate_paid(
    total_paid: Decimal,
    items: list[dict[str, Any]],
) -> list[Decimal]:
    total_units = sum(int(item["quantity"]) for item in items)
    if total_units <= 0:
        return [Decimal("0.00") for _ in items]
    allocated: list[Decimal] = []
    used = Decimal("0.00")
    for index, item in enumerate(items):
        if index == len(items) - 1:
            share = total_paid - used
        else:
            share = (
                total_paid * Decimal(int(item["quantity"])) / Decimal(total_units)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            used += share
        allocated.append(share)
    return allocated

from __future__ import annotations

import re
from datetime import datetime

from adwatch.orders.models import ParseResult, PlatformSku


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

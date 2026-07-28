from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class PlatformOrderLine:
    platform: str
    store: str
    order_id: str
    item_id: str
    model_id: str
    seller_sku: str
    variation_name: str
    product_name: str
    quantity: int
    buyer_paid: Decimal
    currency: str
    order_status: str
    logistics_status: str
    refund_status: str
    ordered_at: date
    source_updated_at: datetime


@dataclass(frozen=True)
class PlatformSku:
    platform: str
    store: str
    item_id: str
    model_id: str
    seller_sku: str
    variation_name: str
    product_name: str
    inventory_units: int
    observed_at: datetime


@dataclass(frozen=True)
class ParseResult:
    orders: tuple[PlatformOrderLine, ...] = ()
    skus: tuple[PlatformSku, ...] = ()
    rejected: tuple[str, ...] = ()

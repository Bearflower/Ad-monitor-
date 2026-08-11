from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from adwatch.integrations.commerce import (
    LogisticsObservation,
    RefundObservation,
)
from adwatch.integrations.inventory import PlatformInventorySnapshot
from adwatch.orders.models import PlatformOrderLine, PlatformSku


def order_observations(
    lines: Iterable[PlatformOrderLine],
    *,
    observed_at: datetime,
) -> tuple[
    tuple[RefundObservation, ...],
    tuple[LogisticsObservation, ...],
]:
    """把多 SKU 订单行归并为订单级退款与物流观测。"""
    refunds: dict[tuple[str, str, str], RefundObservation] = {}
    logistics: dict[tuple[str, str, str], LogisticsObservation] = {}
    for line in lines:
        key = (line.platform, line.store, line.order_id)
        logistics_status = line.logistics_status.strip()
        if logistics_status:
            logistics[key] = LogisticsObservation(
                platform=line.platform,
                store=line.store,
                order_id=line.order_id,
                observed_on=observed_at.date(),
                order_date=line.ordered_at,
                logistics_status=logistics_status,
                source_updated_at=line.source_updated_at,
                observed_at=observed_at,
                source="shopee_order_card",
            )
        refund_status = line.refund_status.strip()
        if refund_status:
            refunds[key] = RefundObservation(
                platform=line.platform,
                store=line.store,
                order_id=line.order_id,
                observed_on=observed_at.date(),
                order_date=line.ordered_at,
                refund_status=refund_status,
                amount_original=None,
                currency=line.currency,
                source_updated_at=line.source_updated_at,
                observed_at=observed_at,
                source="shopee_order_card",
            )
    return tuple(refunds.values()), tuple(logistics.values())


def inventory_observations(
    skus: Iterable[PlatformSku],
    *,
    observed_at: datetime,
) -> tuple[PlatformInventorySnapshot, ...]:
    """从稳定 Item/Model/Seller SKU 映射生成平台库存日快照。"""
    return tuple(
        PlatformInventorySnapshot(
            platform=sku.platform,
            store=sku.store,
            item_id=sku.item_id,
            model_id=sku.model_id,
            seller_sku=sku.seller_sku,
            snapshot_date=observed_at.date(),
            available_units=sku.inventory_units,
            observed_at=observed_at,
            source="shopee_product_api",
        )
        for sku in skus
    )

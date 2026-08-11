from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.orders.models import PlatformOrderLine, PlatformSku
from adwatch.orders.observations import (
    inventory_observations,
    order_observations,
)


def _order_line(
    *,
    order_id: str = "ORDER-1",
    model_id: str = "model-1",
    order_status: str = "completed",
    logistics_status: str = "delivered",
    refund_status: str = "returned",
) -> PlatformOrderLine:
    return PlatformOrderLine(
        platform="shopee",
        store="shop",
        order_id=order_id,
        item_id="item-1",
        model_id=model_id,
        seller_sku=f"SKU-{model_id}",
        variation_name="Blue",
        product_name="Product",
        quantity=1,
        buyer_paid=Decimal(10),
        currency="THB",
        order_status=order_status,
        logistics_status=logistics_status,
        refund_status=refund_status,
        ordered_at=date(2026, 8, 1),
        source_updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_order_lines_create_one_observation_per_order():
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    lines = (
        _order_line(model_id="model-1"),
        _order_line(model_id="model-2"),
    )

    refunds, logistics = order_observations(
        lines,
        observed_at=observed_at,
    )

    assert len(refunds) == 1
    assert refunds[0].refund_status == "returned"
    assert refunds[0].amount_original is None
    assert refunds[0].observed_on == date(2026, 8, 4)
    assert len(logistics) == 1
    assert logistics[0].logistics_status == "delivered"


def test_cancelled_order_is_not_assumed_to_be_a_refund():
    refunds, logistics = order_observations(
        (
            _order_line(
                order_status="cancelled",
                logistics_status="",
                refund_status="",
            ),
        ),
        observed_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
    )

    assert refunds == ()
    assert logistics == ()


def test_skus_create_daily_inventory_snapshots():
    observed_at = datetime(2026, 8, 4, 9, tzinfo=UTC)
    sku = PlatformSku(
        platform="shopee",
        store="shop",
        item_id="item-1",
        model_id="model-1",
        seller_sku="SKU-1",
        variation_name="Blue",
        product_name="Product",
        inventory_units=8,
        observed_at=observed_at,
    )

    snapshots = inventory_observations((sku,), observed_at=observed_at)

    assert len(snapshots) == 1
    assert snapshots[0].snapshot_date == date(2026, 8, 4)
    assert snapshots[0].available_units == 8
    assert snapshots[0].seller_sku == "SKU-1"

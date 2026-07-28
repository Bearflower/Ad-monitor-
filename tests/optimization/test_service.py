from decimal import Decimal

from adwatch.optimization.models import OptimizationInput
from adwatch.optimization.service import analyze_optimization


def test_three_roas_exclude_cancelled_refunded_and_review_orders():
    result = analyze_optimization(
        OptimizationInput(
            attributed_gmv=Decimal(1000),
            ad_spend=Decimal(100),
            settled_sales=Decimal(900),
            cancelled_sales=Decimal(100),
            refunded_sales=Decimal(50),
            review_order_sales=Decimal(50),
            product_cost=Decimal(300),
            platform_fees=Decimal(80),
            seller_shipping=Decimal(20),
            coupons=Decimal(10),
            inventory_units=30,
            expected_daily_units=Decimal(3),
            attribution_capability="campaign_only",
        )
    )

    assert result.platform_roas == Decimal("10.0000")
    assert result.real_net_sales == Decimal(700)
    assert result.net_sales_roas == Decimal("7.0000")
    assert result.pre_ad_contribution_margin == Decimal(290)
    assert result.profit_roas == Decimal("2.9000")
    assert result.post_ad_net_profit == Decimal(190)
    assert result.inventory_cover_days == Decimal("10.0000")
    assert result.attribution_capability == "campaign_only"


def test_zero_spend_and_missing_inventory_reduce_confidence():
    result = analyze_optimization(
        OptimizationInput(
            attributed_gmv=Decimal(0),
            ad_spend=Decimal(0),
            settled_sales=Decimal(100),
            cancelled_sales=Decimal(0),
            refunded_sales=Decimal(0),
            review_order_sales=Decimal(0),
            product_cost=Decimal(20),
            platform_fees=Decimal(5),
            seller_shipping=Decimal(0),
            coupons=Decimal(0),
            inventory_units=None,
            expected_daily_units=None,
            attribution_capability="campaign_only",
        )
    )

    assert result.platform_roas is None
    assert result.profit_roas is None
    assert result.confidence_level == "financial_only"
    assert "inventory_missing" in result.evidence

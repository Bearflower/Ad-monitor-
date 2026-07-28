from decimal import ROUND_HALF_UP, Decimal

from adwatch.optimization.models import OptimizationInput, OptimizationResult


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return (numerator / denominator).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def analyze_optimization(data: OptimizationInput) -> OptimizationResult:
    real_net_sales = max(
        Decimal(0),
        data.settled_sales
        - data.cancelled_sales
        - data.refunded_sales
        - data.review_order_sales,
    )
    contribution = (
        real_net_sales
        - data.product_cost
        - data.platform_fees
        - data.seller_shipping
        - data.coupons
    )
    cover = None
    evidence = [
        f"attribution:{data.attribution_capability}",
        f"review_orders_excluded:{data.review_order_sales}",
        f"refunds_excluded:{data.refunded_sales}",
    ]
    if (
        data.inventory_units is not None
        and data.expected_daily_units is not None
        and data.expected_daily_units > 0
    ):
        cover = _ratio(
            Decimal(data.inventory_units), data.expected_daily_units
        )
        confidence = "inventory_safe"
        evidence.append(f"inventory_units:{data.inventory_units}")
    else:
        confidence = "financial_only"
        evidence.append("inventory_missing")
    return OptimizationResult(
        platform_roas=_ratio(data.attributed_gmv, data.ad_spend),
        net_sales_roas=_ratio(real_net_sales, data.ad_spend),
        profit_roas=_ratio(contribution, data.ad_spend),
        real_net_sales=real_net_sales,
        pre_ad_contribution_margin=contribution,
        post_ad_net_profit=contribution - data.ad_spend,
        inventory_cover_days=cover,
        confidence_level=confidence,
        attribution_capability=data.attribution_capability,
        evidence=tuple(evidence),
    )

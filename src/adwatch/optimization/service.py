from decimal import ROUND_HALF_UP, Decimal

from adwatch.optimization.models import (
    OptimizationDiagnostic,
    OptimizationInput,
    OptimizationResult,
)


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


def diagnose_optimization(
    *,
    platform_roas: Decimal | None,
    profit_roas: Decimal | None,
    post_ad_net_profit: Decimal,
    refund_rate: Decimal,
    inventory_cover_days: Decimal | None,
    spend_change_ratio: Decimal,
    order_change_ratio: Decimal,
    ad_balance_cover_days: Decimal | None,
    confidence_level: str,
) -> tuple[OptimizationDiagnostic, ...]:
    found: list[OptimizationDiagnostic] = []

    def add(code: str, severity: str, message: str, *evidence: str) -> None:
        found.append(
            OptimizationDiagnostic(code, severity, message, tuple(evidence))
        )

    if (
        platform_roas is not None
        and platform_roas > 1
        and (profit_roas is None or post_ad_net_profit < 0)
    ):
        add(
            "platform_roas_profit_mismatch",
            "high",
            "平台 ROAS 良好但广告后净利润为负",
            f"platform_roas={platform_roas}",
            f"post_ad_net_profit={post_ad_net_profit}",
        )
    if refund_rate >= Decimal("0.30"):
        add(
            "high_refund_rate",
            "high",
            "退款率过高",
            f"refund_rate={refund_rate}",
        )
    if inventory_cover_days is not None and inventory_cover_days < 7:
        add(
            "inventory_risk",
            "high",
            "库存覆盖不足七天",
            f"inventory_cover_days={inventory_cover_days}",
        )
    if spend_change_ratio >= Decimal("0.50") and order_change_ratio <= 0:
        add(
            "spend_without_order_growth",
            "high",
            "花费增长但订单未增长",
            f"spend_change_ratio={spend_change_ratio}",
            f"order_change_ratio={order_change_ratio}",
        )
    if ad_balance_cover_days is not None and ad_balance_cover_days < 2:
        add(
            "ad_balance_low",
            "medium",
            "广告余额不足两天",
            f"ad_balance_cover_days={ad_balance_cover_days}",
        )
    if confidence_level not in {"verified", "inventory_safe"}:
        add(
            "data_incomplete",
            "medium",
            "经营数据可信度不足",
            f"confidence_level={confidence_level}",
        )
    return tuple(found)

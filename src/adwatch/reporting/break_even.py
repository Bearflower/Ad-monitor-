from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal


@dataclass(frozen=True)
class BreakEvenTarget:
    break_even_roas: Decimal | None
    break_even_gmv: Decimal | None
    average_order_value: Decimal | None
    break_even_orders: int | None
    gmv_gap: Decimal | None
    order_gap: int | None
    confidence: str
    explanation: str


def _unavailable(explanation: str) -> BreakEvenTarget:
    return BreakEvenTarget(
        break_even_roas=None,
        break_even_gmv=None,
        average_order_value=None,
        break_even_orders=None,
        gmv_gap=None,
        order_gap=None,
        confidence="missing_data",
        explanation=explanation,
    )


def calculate_break_even(
    *,
    spend: Decimal,
    gmv: Decimal,
    orders: int,
    attributed_sales_cny: Decimal | None,
    platform_fee_cny: Decimal | None,
    variable_cost_cny: Decimal | None,
    matched_cost_orders: int,
) -> BreakEvenTarget:
    if (
        attributed_sales_cny is None
        or platform_fee_cny is None
        or variable_cost_cny is None
    ):
        return _unavailable("汇率、费率或 SKU 成本不足")
    if spend <= 0 or gmv <= 0 or orders <= 0 or attributed_sales_cny <= 0:
        return _unavailable("广告花费、GMV 或订单数不足")

    contribution = (
        attributed_sales_cny - platform_fee_cny - variable_cost_cny
    )
    if contribution <= 0:
        return _unavailable("广告前贡献利润率小于或等于零")

    contribution_margin = contribution / attributed_sales_cny
    break_even_roas_raw = Decimal(1) / contribution_margin
    break_even_gmv_raw = spend * break_even_roas_raw
    average_order_value = gmv / Decimal(orders)
    break_even_orders = int(
        (break_even_gmv_raw / average_order_value).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    confidence = (
        "verified"
        if matched_cost_orders >= orders
        else "reconciliation_pending"
    )
    explanation = (
        f"广告归因 {orders} 单，"
        + (
            f"已匹配 {matched_cost_orders} 个实际成本订单"
            if confidence == "verified"
            else f"当前匹配 {matched_cost_orders} 个实际成本订单"
        )
    )
    return BreakEvenTarget(
        break_even_roas=break_even_roas_raw.quantize(Decimal("0.01")),
        break_even_gmv=break_even_gmv_raw.quantize(Decimal("0.01")),
        average_order_value=average_order_value.quantize(Decimal("0.01")),
        break_even_orders=break_even_orders,
        gmv_gap=max(break_even_gmv_raw - gmv, Decimal(0)).quantize(
            Decimal("0.01")
        ),
        order_gap=max(break_even_orders - orders, 0),
        confidence=confidence,
        explanation=explanation,
    )

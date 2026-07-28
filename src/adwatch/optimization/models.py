from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OptimizationInput:
    attributed_gmv: Decimal
    ad_spend: Decimal
    settled_sales: Decimal
    cancelled_sales: Decimal
    refunded_sales: Decimal
    review_order_sales: Decimal
    product_cost: Decimal
    platform_fees: Decimal
    seller_shipping: Decimal
    coupons: Decimal
    inventory_units: int | None
    expected_daily_units: Decimal | None
    attribution_capability: str


@dataclass(frozen=True)
class OptimizationResult:
    platform_roas: Decimal | None
    net_sales_roas: Decimal | None
    profit_roas: Decimal | None
    real_net_sales: Decimal
    pre_ad_contribution_margin: Decimal
    post_ad_net_profit: Decimal
    inventory_cover_days: Decimal | None
    confidence_level: str
    attribution_capability: str
    evidence: tuple[str, ...]

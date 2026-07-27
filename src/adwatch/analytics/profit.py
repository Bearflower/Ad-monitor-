from dataclasses import dataclass
from decimal import Decimal

MONEY = Decimal("0.01")
RATIO = Decimal("0.0001")


@dataclass(frozen=True)
class ProfitInput:
    gmv: Decimal
    refunds: Decimal
    commission_rate: Decimal
    product_cost: Decimal
    ad_spend: Decimal
    seller_shipping: Decimal
    coupons: Decimal
    allocated_fixed_cost: Decimal
    exchange_rate_to_cny: Decimal
    product_cost_cny: Decimal | None = None

    @classmethod
    def zero(cls) -> "ProfitInput":
        zero = Decimal("0")
        return cls(
            gmv=zero,
            refunds=zero,
            commission_rate=zero,
            product_cost=zero,
            ad_spend=zero,
            seller_shipping=zero,
            coupons=zero,
            allocated_fixed_cost=zero,
            exchange_rate_to_cny=Decimal("1"),
        )


@dataclass(frozen=True)
class ProfitResult:
    net_sales_cny: Decimal
    platform_commission_cny: Decimal
    gross_profit_cny: Decimal
    net_profit_cny: Decimal
    break_even_roas: Decimal | None


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY)


def calculate_profit(item: ProfitInput) -> ProfitResult:
    net_sales = item.gmv - item.refunds
    commission = net_sales * item.commission_rate
    rate = item.exchange_rate_to_cny
    net_sales_cny = net_sales * rate
    commission_cny = commission * rate
    resolved_product_cost_cny = (
        item.product_cost * rate
        if item.product_cost_cny is None
        else item.product_cost_cny
    )
    gross_profit_cny = (
        net_sales_cny - commission_cny - resolved_product_cost_cny
    )
    ad_spend_cny = item.ad_spend * rate
    other_costs_cny = (
        item.seller_shipping + item.coupons + item.allocated_fixed_cost
    ) * rate
    net_profit_cny = gross_profit_cny - ad_spend_cny - other_costs_cny

    break_even_roas = None
    contribution_rate = Decimal("1") - item.commission_rate
    if ad_spend_cny != 0 and contribution_rate > 0:
        break_even_gmv_cny = (
            resolved_product_cost_cny + other_costs_cny + ad_spend_cny
        ) / contribution_rate
        break_even_roas = (
            break_even_gmv_cny / ad_spend_cny
        ).quantize(RATIO)

    return ProfitResult(
        net_sales_cny=_money(net_sales_cny),
        platform_commission_cny=_money(commission_cny),
        gross_profit_cny=_money(gross_profit_cny),
        net_profit_cny=_money(net_profit_cny),
        break_even_roas=break_even_roas,
    )

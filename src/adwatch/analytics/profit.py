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
    gross_profit = net_sales - commission - item.product_cost
    net_profit = (
        gross_profit
        - item.ad_spend
        - item.seller_shipping
        - item.coupons
        - item.allocated_fixed_cost
    )
    rate = item.exchange_rate_to_cny

    break_even_roas = None
    contribution_rate = Decimal("1") - item.commission_rate
    if item.ad_spend != 0 and contribution_rate > 0:
        non_ad_costs = (
            item.product_cost
            + item.seller_shipping
            + item.coupons
            + item.allocated_fixed_cost
        )
        break_even_gmv = (non_ad_costs + item.ad_spend) / contribution_rate
        break_even_roas = (break_even_gmv / item.ad_spend).quantize(RATIO)

    return ProfitResult(
        net_sales_cny=_money(net_sales * rate),
        platform_commission_cny=_money(commission * rate),
        gross_profit_cny=_money(gross_profit * rate),
        net_profit_cny=_money(net_profit * rate),
        break_even_roas=break_even_roas,
    )

from decimal import Decimal

from adwatch.analytics.profit import ProfitInput, calculate_profit


def test_profit_uses_refunds_commission_costs_and_operating_deductions():
    result = calculate_profit(
        ProfitInput(
            gmv=Decimal("1000"),
            refunds=Decimal("100"),
            commission_rate=Decimal("0.08"),
            product_cost=Decimal("300"),
            ad_spend=Decimal("120"),
            seller_shipping=Decimal("40"),
            coupons=Decimal("20"),
            allocated_fixed_cost=Decimal("30"),
            exchange_rate_to_cny=Decimal("1.50"),
        )
    )
    assert result.net_sales_cny == Decimal("1350.00")
    assert result.platform_commission_cny == Decimal("108.00")
    assert result.gross_profit_cny == Decimal("792.00")
    assert result.net_profit_cny == Decimal("477.00")


def test_zero_spend_has_no_break_even_roas():
    result = calculate_profit(ProfitInput.zero())
    assert result.break_even_roas is None

from decimal import Decimal

import pytest

from adwatch.reporting.break_even import calculate_break_even


def test_calculates_conservative_break_even_target():
    target = calculate_break_even(
        spend=Decimal("210.10"),
        gmv=Decimal("179.00"),
        orders=2,
        attributed_sales_cny=Decimal("36.06"),
        platform_fee_cny=Decimal("8.55"),
        variable_cost_cny=Decimal("9.81"),
        matched_cost_orders=1,
    )

    assert target.break_even_roas == Decimal("2.04")
    assert target.break_even_gmv == Decimal("428.03")
    assert target.average_order_value == Decimal("89.50")
    assert target.break_even_orders == 5
    assert target.gmv_gap == Decimal("249.03")
    assert target.order_gap == 3
    assert target.confidence == "reconciliation_pending"
    assert target.explanation == "广告归因 2 单，当前匹配 1 个实际成本订单"


@pytest.mark.parametrize(
    (
        "spend",
        "gmv",
        "orders",
        "sales",
        "fee",
        "cost",
    ),
    (
        ("0", "179", 2, "36.06", "8.55", "9.81"),
        ("210.10", "0", 2, "36.06", "8.55", "9.81"),
        ("210.10", "179", 0, "36.06", "8.55", "9.81"),
        ("210.10", "179", 2, "0", "8.55", "9.81"),
        ("210.10", "179", 2, "36.06", "30", "9.81"),
    ),
)
def test_returns_missing_data_when_target_cannot_be_calculated(
    spend, gmv, orders, sales, fee, cost
):
    target = calculate_break_even(
        spend=Decimal(spend),
        gmv=Decimal(gmv),
        orders=orders,
        attributed_sales_cny=Decimal(sales),
        platform_fee_cny=Decimal(fee),
        variable_cost_cny=Decimal(cost),
        matched_cost_orders=0,
    )

    assert target.break_even_roas is None
    assert target.break_even_gmv is None
    assert target.break_even_orders is None
    assert target.confidence == "missing_data"


def test_returns_missing_data_when_profit_inputs_are_missing():
    target = calculate_break_even(
        spend=Decimal("210.10"),
        gmv=Decimal(179),
        orders=2,
        attributed_sales_cny=None,
        platform_fee_cny=None,
        variable_cost_cny=None,
        matched_cost_orders=0,
    )

    assert target.confidence == "missing_data"
    assert target.explanation == "汇率、费率或 SKU 成本不足"


def test_marks_complete_cost_order_coverage_as_verified():
    target = calculate_break_even(
        spend=Decimal("210.10"),
        gmv=Decimal(179),
        orders=2,
        attributed_sales_cny=Decimal("36.06"),
        platform_fee_cny=Decimal("8.55"),
        variable_cost_cny=Decimal("9.81"),
        matched_cost_orders=2,
    )

    assert target.confidence == "verified"
    assert target.explanation == "广告归因 2 单，已匹配 2 个实际成本订单"

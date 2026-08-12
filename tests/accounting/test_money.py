from decimal import Decimal

import pytest

from adwatch.accounting.money import (
    MoneyInputError,
    convert_to_cny,
    normalize_currency,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("¥", "CNY"),
        ("￥", "CNY"),
        ("cny", "CNY"),
        ("฿", "THB"),
        ("thb", "THB"),
        ("S$", "SGD"),
    ],
)
def test_normalize_excel_currency_symbols(raw, expected):
    assert normalize_currency(raw) == expected


def test_convert_to_cny_uses_decimal_and_half_up_rounding():
    assert convert_to_cny(Decimal("10.005"), Decimal(1)) == Decimal("10.01")
    assert convert_to_cny(Decimal(100), Decimal("0.2034")) == Decimal("20.34")


def test_unknown_currency_and_non_positive_rate_are_rejected():
    with pytest.raises(MoneyInputError, match="unsupported currency"):
        normalize_currency("USD")
    with pytest.raises(MoneyInputError, match="positive"):
        convert_to_cny(Decimal(10), Decimal(0))

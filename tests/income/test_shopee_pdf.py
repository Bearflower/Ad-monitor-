from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from adwatch.income.shopee_pdf import IncomeParseError, parse_shopee_text

INCOME_TEXT = Path("tests/fixtures/shopee/income_statement.txt")


def test_income_fixture_contains_business_fields_without_pii():
    text = INCOME_TEXT.read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in (
        "name in bank account",
        "bank account",
        "bank name",
        "road",
        "address",
        "phone",
        "/users/",
    ):
        assert forbidden not in lowered
    for required in (
        "Statement for 2026-07-20 to 2026-07-26",
        "Username : store-alias",
        "Product Price 4,473",
        "Commission fee -500",
        "Service Fee -379",
        "Transaction Fee -150",
        "AMS Commission Fee -32",
        "Ads Credit Top-Up (Escrow) -13",
        "Total Payout Released ฿3,364",
    ):
        assert required in text


def test_parse_shopee_statement_reconciles_rate_and_payout():
    statement = parse_shopee_text(
        INCOME_TEXT.read_text(encoding="utf-8"),
        source_name="weekly_report.pdf",
        source_sha256="a" * 64,
    )

    assert statement.platform == "shopee"
    assert statement.store == "store-alias"
    assert statement.period_start == date(2026, 7, 20)
    assert statement.period_end == date(2026, 7, 26)
    assert statement.sales_fee_total == Decimal(1061)
    assert statement.effective_rate == Decimal("0.237201")
    assert statement.ads_credit_topup == Decimal(13)
    assert statement.total_payout == Decimal(3364)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    (
        ("Product Price 4,473", "Product Price 0", "positive"),
        ("Commission fee -500\n", "", "missing field"),
        (
            "Total Payout Released ฿3,364",
            "Total Payout Released ฿3,365",
            "does not reconcile",
        ),
    ),
)
def test_parse_shopee_statement_rejects_invalid_or_unbalanced_data(
    old,
    new,
    message,
):
    text = INCOME_TEXT.read_text(encoding="utf-8").replace(old, new)

    with pytest.raises(IncomeParseError, match=message):
        parse_shopee_text(
            text,
            source_name="weekly_report.pdf",
            source_sha256="a" * 64,
        )

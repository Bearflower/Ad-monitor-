from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from adwatch.income.models import FeeStatement


class IncomeParseError(ValueError):
    pass


_AMOUNT = r"([\-−–]?\s*฿?\s*[\d,]+(?:\.\d+)?)"


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise IncomeParseError("could not extract Income PDF text") from error
    if not text.strip():
        raise IncomeParseError("Income PDF contains no extractable text")
    return text


def parse_shopee_pdf(path: Path) -> FeeStatement:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise IncomeParseError("could not read Income PDF") from error
    return parse_shopee_text(
        extract_pdf_text(path),
        source_name=path.name,
        source_sha256=digest,
    )


def parse_shopee_text(
    text: str,
    *,
    source_name: str,
    source_sha256: str,
) -> FeeStatement:
    period = re.search(
        r"Statement\s+for\s+(\d{4}-\d{2}-\d{2})\s+to\s+"
        r"(\d{4}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    source_store = re.search(
        r"(?im)^\s*Username\s*:\s*([^\s]+)\s*$",
        text,
    )
    if period is None or source_store is None:
        raise IncomeParseError("statement period or store alias is missing")

    product_price = _required_amount(text, ("Product Price",))
    if product_price <= 0:
        raise IncomeParseError("product price must be positive")
    commission_fee = _required_amount(text, ("Commission fee",), cost=True)
    service_fee = _required_amount(text, ("Service Fee",), cost=True)
    transaction_fee = _required_amount(text, ("Transaction Fee",), cost=True)
    affiliate_fee = _required_amount(
        text,
        ("AMS Commission Fee", "Affiliate Fee"),
        cost=True,
    )
    sales_fee_total = (
        commission_fee + service_fee + transaction_fee + affiliate_fee
    )
    seller_voucher = _required_amount(
        text,
        ("Voucher Sponsored by Seller",),
        cost=True,
    )
    shipping_paid_by_buyer = _required_amount(
        text,
        ("Shipping Fee Paid by Buyer",),
        cost=True,
    )
    shipping_provider_charge = _required_amount(
        text,
        ("Shipping Fee Charged by Logistic Provider",),
        cost=True,
    )
    shipping_rebate = _required_amount(
        text,
        ("Shipping Rebate From Shopee", "Shipping Rebate From Platform"),
        cost=True,
    )
    ads_credit_topup = _required_amount(
        text,
        ("Ads Credit Top-Up (Escrow)",),
        cost=True,
    )
    total_payout = _required_amount(
        text,
        ("Total Payout Released",),
        cost=True,
    )
    adjustment_amount = _optional_amount(
        text,
        ("Adjustment Amount", "Order Adjustment"),
    )
    if adjustment_amount is None:
        if "no payout history within this week" not in text.lower():
            raise IncomeParseError("missing field: Order Adjustment")
        adjustment_amount = Decimal(0)

    try:
        return FeeStatement(
            platform="shopee",
            store=source_store.group(1),
            source_store=source_store.group(1),
            period_start=date.fromisoformat(period.group(1)),
            period_end=date.fromisoformat(period.group(2)),
            currency="THB",
            product_price=product_price,
            commission_fee=commission_fee,
            service_fee=service_fee,
            transaction_fee=transaction_fee,
            affiliate_fee=affiliate_fee,
            sales_fee_total=sales_fee_total,
            effective_rate=(sales_fee_total / product_price).quantize(
                Decimal("0.000001")
            ),
            seller_voucher=seller_voucher,
            shipping_paid_by_buyer=shipping_paid_by_buyer,
            shipping_provider_charge=shipping_provider_charge,
            shipping_rebate=shipping_rebate,
            ads_credit_topup=ads_credit_topup,
            adjustment_amount=adjustment_amount,
            total_payout=total_payout,
            source_name=source_name,
            source_sha256=source_sha256,
        )
    except ValueError as error:
        raise IncomeParseError(str(error)) from error


def _required_amount(
    text: str,
    aliases: tuple[str, ...],
    *,
    cost: bool = False,
) -> Decimal:
    value = _optional_amount(text, aliases)
    if value is None:
        raise IncomeParseError(f"missing field: {aliases[0]}")
    return abs(value) if cost else value


def _optional_amount(
    text: str,
    aliases: tuple[str, ...],
) -> Decimal | None:
    for alias in aliases:
        match = re.search(
            rf"(?im)^\s*{re.escape(alias)}\s+{_AMOUNT}\s*$",
            text,
        )
        if match is None:
            continue
        raw = (
            match.group(1)
            .replace("−", "-")
            .replace("–", "-")
            .replace("฿", "")
            .replace(",", "")
            .replace(" ", "")
        )
        try:
            return Decimal(raw)
        except InvalidOperation as error:
            raise IncomeParseError(f"invalid field: {alias}") from error
    return None

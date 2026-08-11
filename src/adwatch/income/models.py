from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

_RATE_QUANTUM = Decimal("0.000001")
_MONEY_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class FeeStatement:
    platform: str
    store: str
    source_store: str
    period_start: date
    period_end: date
    currency: str
    product_price: Decimal
    commission_fee: Decimal
    service_fee: Decimal
    transaction_fee: Decimal
    affiliate_fee: Decimal
    sales_fee_total: Decimal
    effective_rate: Decimal
    seller_voucher: Decimal
    shipping_paid_by_buyer: Decimal
    shipping_provider_charge: Decimal
    shipping_rebate: Decimal
    ads_credit_topup: Decimal
    adjustment_amount: Decimal
    total_payout: Decimal
    source_name: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not all(
            (self.platform, self.store, self.source_store, self.currency)
        ):
            raise ValueError("income statement identity fields are required")
        if self.period_start > self.period_end:
            raise ValueError("income statement period is invalid")
        if self.product_price <= 0:
            raise ValueError("product price must be positive")
        nonnegative = (
            self.commission_fee,
            self.service_fee,
            self.transaction_fee,
            self.affiliate_fee,
            self.sales_fee_total,
            self.seller_voucher,
            self.shipping_paid_by_buyer,
            self.shipping_provider_charge,
            self.shipping_rebate,
            self.ads_credit_topup,
            self.total_payout,
        )
        if any(value < 0 for value in nonnegative):
            raise ValueError("income statement costs must be nonnegative")
        calculated_fees = (
            self.commission_fee
            + self.service_fee
            + self.transaction_fee
            + self.affiliate_fee
        )
        if abs(calculated_fees - self.sales_fee_total) > _MONEY_TOLERANCE:
            raise ValueError("sales fee total does not reconcile")
        calculated_rate = (
            self.sales_fee_total / self.product_price
        ).quantize(_RATE_QUANTUM)
        if calculated_rate != self.effective_rate:
            raise ValueError("effective rate does not reconcile")
        expected_payout = (
            self.product_price
            - self.seller_voucher
            + self.shipping_paid_by_buyer
            - self.shipping_provider_charge
            + self.shipping_rebate
            - self.sales_fee_total
            - self.ads_credit_topup
            + self.adjustment_amount
        )
        if abs(expected_payout - self.total_payout) > _MONEY_TOLERANCE:
            raise ValueError("total payout does not reconcile")
        if not (Decimal(0) <= self.effective_rate <= Decimal(1)):
            raise ValueError("effective rate must be between zero and one")
        if Path(self.source_name).name != self.source_name:
            raise ValueError("income source_name must not contain a path")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.source_name):
            raise ValueError("income source_name must not contain control characters")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise ValueError("income source SHA-256 is invalid")


@dataclass(frozen=True)
class ResolvedFeeRate:
    rate: Decimal
    period_start: date
    period_end: date
    selection: str
    source_name: str
    source_sha256: str

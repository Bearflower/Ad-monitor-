from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ExpenseDraft:
    occurred_on: date
    category: str
    amount_original: Decimal
    currency: str
    rate_to_cny: Decimal
    payer: str
    fund_nature: str
    affects_profit: bool
    affects_capital: bool
    note: str = ""


@dataclass(frozen=True)
class ExpenseEntry:
    id: str
    status: str
    amount_cny: Decimal


@dataclass(frozen=True)
class ManualIncomeDraft:
    occurred_on: date
    amount_original: Decimal
    currency: str
    rate_to_cny: Decimal
    platform: str = ""
    store: str = ""
    order_id: str = ""
    rate_date: date | None = None
    rate_source: str = "manual"
    note: str = ""


@dataclass(frozen=True)
class AdFundingDraft:
    occurred_on: date
    platform: str
    store: str
    entry_type: str
    paid_amount_original: Decimal
    credited_amount_original: Decimal
    currency: str
    rate_to_cny: Decimal
    source: str
    rate_date: date | None = None
    rate_source: str = "manual"


@dataclass(frozen=True)
class ReviewCostDraft:
    occurred_on: date
    platform: str
    store: str
    order_id: str
    seller_sku: str
    goods_cost_original: Decimal
    service_fee_original: Decimal
    currency: str
    rate_to_cny: Decimal
    rate_date: date | None = None
    rate_source: str = "manual"


@dataclass(frozen=True)
class CapitalDraft:
    occurred_on: date
    partner: str
    entry_type: str
    entry_name: str
    amount_original: Decimal
    currency: str
    rate_to_cny: Decimal
    rate_date: date | None = None
    rate_source: str = "manual"
    note: str = ""


@dataclass(frozen=True)
class FinancialAdjustment:
    id: str
    adjustment_type: str
    amount_cny: Decimal
    occurred_on: date
    reason: str
    status: str
    reversal_of: str | None
    created_by: str
    created_at: str
    evidence_ids: tuple[str, ...] = ()

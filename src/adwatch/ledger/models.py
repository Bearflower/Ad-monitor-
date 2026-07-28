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

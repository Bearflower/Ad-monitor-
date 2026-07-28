from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PurchaseLine:
    seller_sku: str
    quantity: int
    unit_cost_cny: Decimal

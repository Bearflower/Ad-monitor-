from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PurchaseLine:
    seller_sku: str
    quantity: int
    unit_cost_original: Decimal
    product_name: str = ""

    @property
    def unit_cost_cny(self) -> Decimal:
        """Compatibility alias for legacy CNY callers."""
        return self.unit_cost_original

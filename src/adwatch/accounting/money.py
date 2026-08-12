from decimal import ROUND_HALF_UP, Decimal


class MoneyInputError(ValueError):
    pass


_CURRENCIES = {
    "¥": "CNY",
    "￥": "CNY",
    "CNY": "CNY",
    "฿": "THB",
    "THB": "THB",
    "S$": "SGD",
    "SGD": "SGD",
}


def normalize_currency(raw: str) -> str:
    normalized = _CURRENCIES.get(raw.strip().upper())
    if normalized is None:
        raise MoneyInputError(f"unsupported currency: {raw}")
    return normalized


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def convert_to_cny(
    amount_original: Decimal,
    rate_to_cny: Decimal,
) -> Decimal:
    if amount_original < 0 or rate_to_cny <= 0:
        raise MoneyInputError(
            "amount must be non-negative and rate must be positive"
        )
    return money(amount_original * rate_to_cny)

from datetime import date

from adwatch.domain import DailyAdMetric, ValidatedMetric, ValidationIssue


SUPPORTED_CURRENCIES = {
    "CNY",
    "USD",
    "MYR",
    "THB",
    "PHP",
    "IDR",
    "VND",
    "SGD",
    "BRL",
}


def validate_metric(metric: DailyAdMetric) -> ValidatedMetric:
    issues: list[ValidationIssue] = []

    identifiers = (
        ("store", metric.store),
        ("account_id", metric.account_id),
        ("campaign_id", metric.campaign_id),
        ("sku_id", metric.sku_id),
    )
    for field, value in identifiers:
        if not value.strip():
            issues.append(
                ValidationIssue(
                    code=f"missing_{field}",
                    field=field,
                    message=f"{field} must not be blank",
                    severity="error",
                )
            )

    numeric_fields = (
        ("spend", metric.spend),
        ("attributed_gmv", metric.attributed_gmv),
        ("orders", metric.orders),
    )
    for field, value in numeric_fields:
        if value < 0:
            issues.append(
                ValidationIssue(
                    code=f"negative_{field}",
                    field=field,
                    message=f"{field} must not be negative",
                    severity="error",
                )
            )

    if metric.data_date > date.today():
        issues.append(
            ValidationIssue(
                code="future_data_date",
                field="data_date",
                message="data_date must not be in the future",
                severity="error",
            )
        )

    if metric.currency not in SUPPORTED_CURRENCIES:
        issues.append(
            ValidationIssue(
                code="unknown_currency",
                field="currency",
                message=f"unsupported currency: {metric.currency}",
                severity="error",
            )
        )

    return ValidatedMetric(metric=metric, issues=tuple(issues))

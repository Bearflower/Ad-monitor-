from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Anomaly:
    code: str
    severity: str
    message: str


def detect_anomalies(
    *,
    current_spend: Decimal,
    baseline_spend: Decimal | None,
    current_roas: Decimal | None,
    baseline_roas: Decimal | None,
    inventory_units: int,
    expected_daily_units: int | Decimal,
) -> tuple[Anomaly, ...]:
    anomalies: list[Anomaly] = []
    if (
        baseline_spend is not None
        and baseline_spend > 0
        and current_spend > baseline_spend * Decimal("1.30")
    ):
        anomalies.append(
            Anomaly(
                code="spend_jump",
                severity="warning",
                message="Spend increased by more than 30%",
            )
        )
    if (
        baseline_roas is not None
        and baseline_roas > 0
        and current_roas is not None
        and current_roas < baseline_roas * Decimal("0.80")
    ):
        anomalies.append(
            Anomaly(
                code="roas_drop",
                severity="warning",
                message="ROAS dropped by more than 20%",
            )
        )
    if expected_daily_units > 0:
        cover_days = Decimal(inventory_units) / Decimal(expected_daily_units)
        if cover_days < 7:
            anomalies.append(
                Anomaly(
                    code="inventory_risk",
                    severity="critical",
                    message="Inventory cover is below 7 days",
                )
            )
    return tuple(anomalies)

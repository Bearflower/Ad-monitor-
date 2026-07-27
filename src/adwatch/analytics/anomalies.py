from dataclasses import dataclass
from datetime import date
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
    platform: str | None = None,
    campaign_start: date | None = None,
    data_date: date | None = None,
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
    if (
        platform
        and campaign_start
        and data_date
        and current_spend == 0
        and baseline_spend is not None
        and baseline_spend > 0
    ):
        learning_days = 7 if platform == "tiktok" else 14
        if (data_date - campaign_start).days < learning_days:
            anomalies.append(
                Anomaly(
                    code="learning_interruption",
                    severity="critical",
                    message="Campaign spend stopped during learning period",
                )
            )
    return tuple(anomalies)

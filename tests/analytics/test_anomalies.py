from datetime import date
from decimal import Decimal

from adwatch.analytics.anomalies import detect_anomalies


def test_spend_jump_and_roas_drop_are_detected():
    codes = {
        item.code
        for item in detect_anomalies(
            current_spend=Decimal(150),
            baseline_spend=Decimal(100),
            current_roas=Decimal("1.5"),
            baseline_roas=Decimal("2.0"),
            inventory_units=50,
            expected_daily_units=5,
        )
    }
    assert codes == {"spend_jump", "roas_drop"}


def test_learning_interruption_is_detected_when_spend_stops():
    codes = {
        item.code
        for item in detect_anomalies(
            current_spend=Decimal(0),
            baseline_spend=Decimal(100),
            current_roas=None,
            baseline_roas=Decimal(2),
            inventory_units=0,
            expected_daily_units=0,
            platform="tiktok",
            campaign_start=date(2026, 7, 20),
            data_date=date(2026, 7, 23),
        )
    }

    assert "learning_interruption" in codes

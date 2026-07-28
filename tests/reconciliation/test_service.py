from datetime import date
from decimal import Decimal

from adwatch.reconciliation.service import ReconciliationService
from adwatch.storage.db import Database


def test_three_day_gate_requires_each_day_at_least_ninety_nine_percent(tmp_path):
    database = Database(tmp_path / "reconcile.sqlite3")
    database.migrate()
    service = ReconciliationService(database)
    expected = {
        "spend": Decimal(100),
        "gmv": Decimal(500),
        "orders": 10,
        "campaign_status": "active",
    }
    for day in (26, 27, 28):
        service.record_day(
            platform="shopee",
            store="shop",
            data_date=date(2026, 7, day),
            expected=expected,
            actual=dict(expected),
        )
    assert service.three_day_ready(
        platform="shopee", store="shop", through=date(2026, 7, 28)
    )


def test_difference_has_category_and_blocks_gate(tmp_path):
    database = Database(tmp_path / "reconcile.sqlite3")
    database.migrate()
    service = ReconciliationService(database)
    for day in (26, 27, 28):
        service.record_day(
            platform="shopee",
            store="shop",
            data_date=date(2026, 7, day),
            expected={"orders": 10},
            actual={"orders": 9 if day == 28 else 10},
            difference_categories={"orders": "attribution"},
        )
    report = service.report(
        platform="shopee",
        store="shop",
        start=date(2026, 7, 26),
        end=date(2026, 7, 28),
    )
    assert report[-1].accuracy == Decimal("0.0000")
    assert report[-1].differences[0].category == "attribution"
    assert not service.three_day_ready(
        platform="shopee", store="shop", through=date(2026, 7, 28)
    )

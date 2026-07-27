import csv
from datetime import date
from decimal import Decimal

import pytest

from adwatch.analytics.business_inputs import (
    BusinessInputError,
    export_business_template,
    import_business_inputs,
    import_minimal_business_inputs,
)
from adwatch.analytics.service import AnalysisService
from adwatch.domain import DailyAdMetric, Platform
from adwatch.pipeline.runner import PipelineRunner
from adwatch.storage.db import Database


class OneMetricCollector:
    source = "ziniao"
    platform = Platform.SHOPEE

    def collect(self, data_date):
        return [
            DailyAdMetric(
                platform=self.platform,
                store="虾皮泰国",
                account_id="account",
                campaign_id="Shop GMV Max",
                sku_id="__ALL__",
                data_date=data_date,
                currency="THB",
                spend=Decimal("105.49"),
                attributed_gmv=Decimal("310"),
                orders=5,
                source="ziniao-cli",
            )
        ]


def _database(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    PipelineRunner(database).run(
        OneMetricCollector(), date(2026, 7, 23)
    )
    return database


def test_export_template_contains_real_metric_identity_and_blank_inputs(
    tmp_path,
):
    database = _database(tmp_path)
    destination = tmp_path / "business.csv"

    count = export_business_template(
        database,
        start=date(2026, 7, 23),
        end=date(2026, 7, 23),
        destination=destination,
    )

    row = next(csv.DictReader(destination.open(encoding="utf-8-sig")))
    assert count == 1
    assert row["campaign_id"] == "Shop GMV Max"
    assert row["sku_id"] == "__ALL__"
    assert row["product_cost"] == ""
    assert row["target_roas"] == ""


def test_import_business_inputs_enables_profit_analysis(tmp_path):
    database = _database(tmp_path)
    source = tmp_path / "business.csv"
    export_business_template(
        database,
        start=date(2026, 7, 23),
        end=date(2026, 7, 23),
        destination=source,
    )
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig")))
    rows[0].update(
        {
            "product_cost": "120",
            "commission_rate": "0.08",
            "seller_shipping": "10",
            "coupons": "5",
            "allocated_fixed_cost": "8",
            "refund_amount": "0",
            "inventory_units": "500",
            "expected_daily_units": "20",
            "rate_to_cny": "0.21",
            "start_date": "2026-07-01",
            "target_roas": "4.4",
            "current_budget": "400",
            "baseline_budget": "400",
        }
    )
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    assert import_business_inputs(database, source) == 1
    summary = AnalysisService(database).run(date(2026, 7, 23))

    assert summary.profit_results == 1
    assert summary.alerts == 0


def test_import_rejects_incomplete_rows_without_partial_writes(tmp_path):
    database = _database(tmp_path)
    source = tmp_path / "bad.csv"
    source.write_text(
        "data_date,platform,store,campaign_id,sku_id,currency\n"
        "2026-07-23,shopee,虾皮泰国,Shop GMV Max,__ALL__,THB\n",
        encoding="utf-8",
    )

    with pytest.raises(BusinessInputError, match="missing columns"):
        import_business_inputs(database, source)


def test_minimal_business_input_maps_unique_metric_for_date(tmp_path):
    database = _database(tmp_path)
    source = tmp_path / "minimal.csv"
    source.write_text(
        "data_date,total_product_cost,refund_amount\n"
        "2026-07-23,120,0\n",
        encoding="utf-8",
    )

    assert import_minimal_business_inputs(database, source) == 1

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT product_cost, refund_amount, commission_rate
            FROM product_costs
            WHERE sku_id='__ALL__' AND effective_date='2026-07-23'
            """
        ).fetchone()
    assert dict(row) == {
        "product_cost": "120",
        "refund_amount": "0",
        "commission_rate": "0",
    }


def test_minimal_business_input_rejects_ambiguous_date(tmp_path):
    database = _database(tmp_path)
    PipelineRunner(database).run(
        OneMetricCollector(), date(2026, 7, 23)
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, roas, cpa, source
            )
            SELECT platform, store, account_id, 'Second Campaign', 'second',
                   data_date, currency, spend, attributed_gmv, orders,
                   roas, cpa, source
            FROM daily_ad_metrics WHERE data_date='2026-07-23' LIMIT 1
            """
        )
    source = tmp_path / "minimal.csv"
    source.write_text(
        "data_date,total_product_cost,refund_amount\n"
        "2026-07-23,120,0\n",
        encoding="utf-8",
    )

    with pytest.raises(BusinessInputError, match="matches 2 metric rows"):
        import_minimal_business_inputs(database, source)

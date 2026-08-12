import json
from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric, Platform
from adwatch.pipeline.runner import PipelineSummary
from adwatch.reporting.quality import write_quality_report
from adwatch.storage.metrics import ReconciliationResult


def test_quality_report_serializes_quarantined_metric_dates(tmp_path):
    metric = DailyAdMetric(
        platform=Platform.SHOPEE,
        store="shop",
        account_id="account",
        campaign_id="campaign",
        sku_id="SKU-1",
        data_date=date(2026, 8, 10),
        currency="THB",
        spend=Decimal(10),
        attributed_gmv=Decimal(20),
        orders=1,
        source="ziniao-cli",
    )
    summary = PipelineSummary(
        run_id="run-1",
        platform="shopee",
        source="ziniao",
        received=2,
        accepted=1,
        quarantined=1,
        reconciliation=ReconciliationResult(
            status="exceeded",
            overview_spend=Decimal(20),
            detail_spend=Decimal(10),
            difference=Decimal(10),
            threshold=Decimal("0.05"),
            details_to_quarantine=(metric,),
        ),
    )

    output = write_quality_report(
        tmp_path, date(2026, 8, 10), "ziniao", [summary]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    quarantined = payload["runs"][0]["reconciliation"][
        "details_to_quarantine"
    ][0]
    assert quarantined["data_date"] == "2026-08-10"

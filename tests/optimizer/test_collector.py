from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from adwatch.config import Settings
from adwatch.optimizer.collector import (
    CollectionResult,
    ShopeeAdCollector,
    ZiniaoShopeeAdsReader,
)
from adwatch.optimizer.database import OptimizerDatabase
from adwatch.optimizer.models import DataQualityStatus
from adwatch.optimizer.repository import OptimizerRepository
from adwatch.optimizer.ziniao import ZiniaoCliClient

DAY = date(2026, 9, 7)


class FakePageReader:
    def __init__(self, *pages: dict[str, object]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[str, date, int]] = []

    def read_page(self, store_id: str, data_date: date, page: int) -> dict[str, object]:
        self.calls.append((store_id, data_date, page))
        return deepcopy(self.pages[page - 1])


def _settings(tmp_path: Path, **changes: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "ziniao_shopee_store_id": "store-1",
        "bridge_retries": 1,
        "bridge_retry_delay_seconds": 1,
        "shopee_ads_max_pages": 5,
        "reconciliation_tolerance": Decimal("0.01"),
    }
    values.update(changes)
    return Settings(**values)


def _synthetic_page() -> dict[str, object]:
    fixture_path = Path(__file__).parent / "fixtures" / "shopee_ads_contract_synthetic.json"
    page = json.loads(fixture_path.read_text(encoding="utf-8"))
    page["overview"] = {
        "currency": "THB",
        "spend": "฿1,000.50",
        "gmv": "฿3,001.50",
        "orders": "10",
        "roas": "2.9990",
    }
    row = page["campaign_rows"][0]
    row.update({"spend": "฿1,000.50", "gmv": "฿3,001.50", "orders": "10", "roas": "2.9990"})
    page["product_rows"] = [
        {
            "campaign_id": "page-campaign-1",
            "campaign_name": "可变展示名",
            "item_id": "item-3",
            "currency": "THB",
            "spend": "฿1,000.50",
            "gmv": "฿3,001.50",
            "orders": "10",
            "roas": "2.9990",
        }
    ]
    page["pagination"] = {"page": "1", "total_pages": "1", "has_next": False}
    return page


@pytest.fixture
def repository(tmp_path: Path) -> OptimizerRepository:
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    return OptimizerRepository(database)


def test_synthetic_fixture_collects_overview_campaign_product_and_snapshot(
    tmp_path: Path, repository: OptimizerRepository,
):
    reader = FakePageReader(_synthetic_page())
    result = ShopeeAdCollector(_settings(tmp_path), reader, repository=repository).collect(DAY)

    assert isinstance(result, CollectionResult)
    assert reader.calls == [("store-1", DAY, 1)]
    assert [(metric.campaign_id, metric.item_id) for metric in result.metrics] == [
        ("__OVERVIEW__", "__ALL__"),
        ("page-campaign-1", "__ALL__"),
        ("page-campaign-1", "item-3"),
    ]
    campaign = result.metrics[1]
    assert (campaign.spend, campaign.gmv, campaign.orders, campaign.roas, campaign.cpa) == (
        Decimal("1000.50"), Decimal("3001.50"), 10, Decimal("3.0000"), Decimal("100.0500"),
    )
    assert result.snapshots[0].campaign_id == "page-campaign-1"
    assert result.quality.status is DataQualityStatus.HEALTHY
    assert result.quality.allow_aggressive_scale is True
    assert repository.list_metrics("store-1", "page-campaign-1", DAY) == (
        result.metrics[1], result.metrics[2],
    )


def test_real_empty_fixture_is_no_data_and_degraded(tmp_path: Path, repository: OptimizerRepository):
    fixture_path = Path(__file__).parent / "fixtures" / "shopee_ads_page.json"
    page = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page), repository=repository).collect(DAY)

    assert result.metrics == ()
    assert result.snapshots == ()
    assert result.quality.status is DataQualityStatus.DEGRADED
    assert result.quality.allow_aggressive_scale is False
    assert "campaign_rows" in result.quality.missing_fields
    with repository.database.connect() as connection:
        assert connection.execute(
            "SELECT status FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
            ("store-1", DAY.isoformat()),
        ).fetchone()[0] == "no_data"


def test_paginates_read_only_and_parses_zero_spend_and_us_format(tmp_path: Path):
    first = _synthetic_page()
    first["pagination"] = {"page": "1", "total_pages": "2", "has_next": True}
    second = _synthetic_page()
    second["captured_at"] = "2026-09-09T01:03:03+00:00"
    second["pagination"] = {"page": "2", "total_pages": "2", "has_next": False}
    second["campaign_rows"][0].update({
        "page_campaign_id": "campaign-2", "item_id": "item-4", "currency": "USD",
        "spend": "$0.00", "gmv": "$1,234.56", "orders": "0", "roas": "0%",
    })
    second["product_rows"][0].update({
        "campaign_id": "campaign-2", "item_id": "item-4", "currency": "USD",
        "spend": "$0.00", "gmv": "$1,234.56", "orders": "0", "roas": "0%",
    })
    second["overview"] = None

    reader = FakePageReader(first, second)
    result = ShopeeAdCollector(_settings(tmp_path), reader).collect(DAY)

    zero_spend = next(metric for metric in result.metrics if metric.campaign_id == "campaign-2" and metric.item_id == "__ALL__")
    assert reader.calls == [("store-1", DAY, 1), ("store-1", DAY, 2)]
    assert (zero_spend.currency, zero_spend.spend, zero_spend.gmv, zero_spend.orders, zero_spend.roas) == (
        "USD", Decimal("0.00"), Decimal("1234.56"), 0, None,
    )


def test_reader_retry_and_wait_come_from_settings(tmp_path: Path):
    class FlakyReader(FakePageReader):
        def read_page(self, store_id: str, data_date: date, page: int) -> dict[str, object]:
            if not self.calls:
                self.calls.append((store_id, data_date, page))
                raise RuntimeError("bridge temporarily unavailable")
            return super().read_page(store_id, data_date, page)

    sleeps: list[float] = []
    reader = FlakyReader(_synthetic_page())
    result = ShopeeAdCollector(
        _settings(tmp_path, bridge_retries=2, bridge_retry_delay_seconds=3),
        reader,
        sleeper=sleeps.append,
    ).collect(DAY)

    assert len(reader.calls) == 2
    assert sleeps == [3]
    assert result.quality.status is DataQualityStatus.HEALTHY


def test_reconciliation_degrades_only_when_same_granularity_differs(tmp_path: Path):
    page = _synthetic_page()
    page["overview"]["spend"] = "1001"

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)

    assert result.quality.status is DataQualityStatus.DEGRADED
    assert result.quality.allow_aggressive_scale is False
    assert "reconciliation:spend" in result.diagnostics
    assert len(result.metrics) == 3  # 对账异常也保留总览、活动与商品各自事实。


def test_reconciliation_does_not_compare_overview_orders_with_campaign_conversions(
    tmp_path: Path,
):
    page = _synthetic_page()
    page["overview"]["orders"] = "7"

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)

    assert result.quality.status is DataQualityStatus.HEALTHY
    assert "reconciliation:orders" not in result.diagnostics


def test_missing_comparable_campaign_summary_degrades_as_unreconcilable(tmp_path: Path):
    page = _synthetic_page()
    page["campaign_rows"] = []
    page["product_rows"] = []

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)

    assert result.quality.status is DataQualityStatus.DEGRADED
    assert "reconciliation:unavailable" in result.diagnostics


def test_malformed_rows_and_ambiguous_campaign_ids_are_not_written(tmp_path: Path):
    page = _synthetic_page()
    duplicate = deepcopy(page["campaign_rows"][0])
    duplicate["name"] = "另一个同 ID 活动"
    page["campaign_rows"].append(duplicate)
    page["product_rows"].append({
        "campaign_id": "unambiguous-malformed", "item_id": "broken", "currency": "THB",
        "spend": "not-a-number", "gmv": "1", "orders": "1",
    })

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)

    assert all(metric.campaign_id != "page-campaign-1" for metric in result.metrics)
    assert result.snapshots == ()
    assert result.quality.status is DataQualityStatus.DEGRADED
    assert "ambiguous_campaign_identity" in result.diagnostics
    assert "metric.spend" in result.quality.missing_fields


def test_duplicate_page_stops_without_duplicate_accumulation(tmp_path: Path):
    page = _synthetic_page()
    page["pagination"] = {"page": "1", "total_pages": "3", "has_next": True}

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page, page)).collect(DAY)

    assert len(result.metrics) == 3
    assert result.quality.status is DataQualityStatus.DEGRADED
    assert "duplicate_page" in result.diagnostics


def test_duplicate_logical_row_is_not_accumulated_across_pages(tmp_path: Path):
    first = _synthetic_page()
    first["pagination"] = {"page": "1", "total_pages": "2", "has_next": True}
    second = _synthetic_page()
    second["captured_at"] = "2026-09-09T01:04:03+00:00"
    second["overview"] = None
    second["pagination"] = {"page": "2", "total_pages": "2", "has_next": False}

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(first, second)).collect(DAY)

    assert [(metric.campaign_id, metric.item_id) for metric in result.metrics].count(
        ("page-campaign-1", "__ALL__")
    ) == 1
    assert "duplicate_metric_row" in result.diagnostics


def test_replace_store_day_is_atomic_and_repeated_collection_is_idempotent(
    tmp_path: Path, repository: OptimizerRepository,
):
    collector = ShopeeAdCollector(_settings(tmp_path), FakePageReader(_synthetic_page()), repository=repository)
    first = collector.collect(DAY)
    second = collector.collect(DAY)

    with repository.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM optimizer_ad_metrics WHERE store_id=? AND data_date=?",
            ("store-1", DAY.isoformat()),
        ).fetchone()[0] == len(first.metrics)
    old_metric = first.metrics[1]
    failing_reader = type(
        "FailingReader",
        (),
        {"read_page": lambda self, store_id, data_date, page: (_ for _ in ()).throw(RuntimeError("read failed"))},
    )()
    with pytest.raises(RuntimeError, match="read failed"):
        ShopeeAdCollector(_settings(tmp_path), failing_reader, repository=repository).collect(DAY)
    assert repository.list_metrics("store-1", old_metric.campaign_id, DAY)[0] == old_metric
    broken_snapshot = type("BrokenSnapshot", (), {"store_id": "store-1"})()
    with pytest.raises(AttributeError):
        repository.replace_store_day("store-1", DAY, (first.metrics[0],), (broken_snapshot,), "success")
    assert repository.list_metrics("store-1", old_metric.campaign_id, DAY)[0] == old_metric
    assert second.metrics == first.metrics


def test_page_limit_is_reported_from_settings(tmp_path: Path):
    page = _synthetic_page()
    page["pagination"] = {"page": "1", "total_pages": "2", "has_next": True}

    result = ShopeeAdCollector(_settings(tmp_path, shopee_ads_max_pages=1), FakePageReader(page)).collect(DAY)

    assert result.quality.status is DataQualityStatus.DEGRADED
    assert "pagination:max_pages" in result.diagnostics


@pytest.mark.parametrize(
    "pages",
    [
        pytest.param(
            lambda: [
                _with_pagination(_synthetic_page(), page="1", total_pages="2", has_next=True)
            ],
            id="maximum_pages_with_next",
        ),
        pytest.param(
            lambda: [_with_pagination(_synthetic_page(), page="1", total_pages="1", has_next=False, malformed=True)],
            id="malformed_pagination",
        ),
        pytest.param(
            lambda: [_without_pagination(_synthetic_page())],
            id="missing_pagination",
        ),
        pytest.param(
            lambda: [_invalid_contract_page()],
            id="invalid_page_contract",
        ),
    ],
)
def test_incomplete_collection_never_replaces_old_facts(
    tmp_path: Path, repository: OptimizerRepository, pages,
):
    seed = ShopeeAdCollector(_settings(tmp_path), FakePageReader(_synthetic_page()), repository=repository).collect(DAY)
    previous = seed.metrics[1]

    result = ShopeeAdCollector(
        _settings(tmp_path, shopee_ads_max_pages=1),
        FakePageReader(*pages()),
        repository=repository,
    ).collect(DAY)

    assert (result.complete, result.persisted) == (False, False)
    assert repository.list_metrics("store-1", previous.campaign_id, DAY)[0] == previous
    with repository.database.connect() as connection:
        assert connection.execute(
            "SELECT status FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
            ("store-1", DAY.isoformat()),
        ).fetchone()[0] == "success"


def test_duplicate_stable_page_with_new_timestamp_is_incomplete_and_preserves_old_facts(
    tmp_path: Path, repository: OptimizerRepository,
):
    seed = ShopeeAdCollector(_settings(tmp_path), FakePageReader(_synthetic_page()), repository=repository).collect(DAY)
    first = _with_pagination(_synthetic_page(), page="1", total_pages="3", has_next=True)
    repeated = _with_pagination(_synthetic_page(), page="2", total_pages="3", has_next=True)
    repeated["captured_at"] = "2026-09-09T01:05:03+00:00"
    first["pagination"]["cursor"] = "page-one-cursor"
    repeated["pagination"]["cursor"] = "page-two-cursor"

    result = ShopeeAdCollector(
        _settings(tmp_path), FakePageReader(first, repeated), repository=repository,
    ).collect(DAY)

    assert result.complete is False
    assert result.persisted is False
    assert "duplicate_page" in result.diagnostics
    assert repository.list_metrics("store-1", seed.metrics[1].campaign_id, DAY)[0] == seed.metrics[1]


@pytest.mark.parametrize(
    ("pages_factory", "diagnostic"),
    [
        pytest.param(
            lambda: [_with_pagination(_synthetic_page(), page="1", total_pages="2", has_next=False)],
            "pagination:contradictory_next",
            id="next_false_before_declared_last_page",
        ),
        pytest.param(
            lambda: [
                _with_pagination(_synthetic_page(), page="1", total_pages="2", has_next=True),
                _with_pagination(_distinct_page(), page="2", total_pages="2", has_next=True),
            ],
            "pagination:contradictory_next",
            id="next_true_at_declared_last_page",
        ),
    ],
)
def test_contradictory_pagination_is_incomplete_and_preserves_success(
    tmp_path: Path, repository: OptimizerRepository, pages_factory, diagnostic: str,
):
    seed = ShopeeAdCollector(_settings(tmp_path), FakePageReader(_synthetic_page()), repository=repository).collect(DAY)

    result = ShopeeAdCollector(
        _settings(tmp_path), FakePageReader(*pages_factory()), repository=repository,
    ).collect(DAY)

    assert (result.complete, result.persisted) == (False, False)
    assert diagnostic in result.diagnostics
    assert repository.list_metrics("store-1", seed.metrics[1].campaign_id, DAY)[0] == seed.metrics[1]
    assert repository.collection_gaps("store-1", DAY, DAY) == ()


def test_incomplete_collection_without_prior_success_is_a_gap(tmp_path: Path, repository: OptimizerRepository):
    incomplete = _synthetic_page()
    incomplete["pagination"] = {"page": "1", "total_pages": "2", "has_next": False}

    result = ShopeeAdCollector(
        _settings(tmp_path), FakePageReader(incomplete), repository=repository,
    ).collect(DAY)

    assert (result.complete, result.persisted) == (False, False)
    assert repository.collection_gaps("store-1", DAY, DAY) == (DAY,)


@pytest.mark.parametrize(
    "pagination",
    [
        {},
        {"page": 0, "total_pages": 1, "has_next": False},
        {"page": 1, "total_pages": 0, "has_next": False},
        {"page": 2, "total_pages": 1, "has_next": False},
        {"page": 1, "total_pages": 1, "has_next": "false"},
        {"page": 1, "total_pages": 2, "has_next": False},
    ],
)
def test_empty_pages_still_require_strict_consistent_pagination(
    tmp_path: Path, repository: OptimizerRepository, pagination,
):
    page = _synthetic_page()
    page["campaign_rows"] = []
    page["product_rows"] = []
    page["overview"] = None
    page["pagination"] = pagination

    result = ShopeeAdCollector(
        _settings(tmp_path), FakePageReader(page), repository=repository,
    ).collect(DAY)

    assert result.complete is False
    assert result.persisted is False
    assert any(
        diagnostic.startswith("pagination:") for diagnostic in result.diagnostics
    )
    assert repository.collection_gaps("store-1", DAY, DAY) == (DAY,)


def test_ziniao_reader_uses_fixed_date_and_page_url_for_first_and_second_page(tmp_path: Path):
    first = _with_pagination(_synthetic_page(), page="1", total_pages="2", has_next=True)
    second = _with_pagination(_synthetic_page(), page="2", total_pages="2", has_next=False)
    first["page_url"] = "https://seller.shopee.co.th/portal/marketing/pas/index?from=1788714000&to=1788800399&page=1"
    second["page_url"] = "https://seller.shopee.co.th/portal/marketing/pas/index?from=1788714000&to=1788800399&page=2"
    first_at_second_url = _with_pagination(
        _synthetic_page(), page="1", total_pages="2", has_next=True
    )
    first_at_second_url["page_url"] = second["page_url"]
    responses = [
        _ok_response({}), _ok_response({"data": {"result": json.dumps(first)}}),
        _ok_response({}),
        _ok_response({"data": {"result": json.dumps(first_at_second_url)}}),
        _ok_response({}),
        _ok_response({"data": {"result": json.dumps(second)}}),
    ]
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return responses.pop(0)

    reader = ZiniaoShopeeAdsReader(ZiniaoCliClient(runner=runner, settings=_settings(tmp_path)))

    assert reader.read_page("store-1", DAY, 1)["pagination"]["page"] == "1"
    assert reader.read_page("store-1", DAY, 2)["pagination"]["page"] == "2"
    urls = [command[command.index("--url") + 1] for command in calls if command[1:3] == ["page", "visit"]]
    assert len(urls) == 2
    assert all(url.startswith("https://seller.shopee.co.th/portal/marketing/pas/index?") for url in urls)
    assert urls[0].endswith("page=1")
    assert urls[1].endswith("page=2")
    page_click = next(
        command for command in calls if command[1:3] == ["page", "click"]
    )
    assert page_click[-2:] == ["--selector", ".eds-pager__button-next"]


def test_ziniao_reader_polls_until_spa_contract_is_ready(tmp_path: Path):
    empty = {
        "page_url": "https://seller.shopee.co.th/portal/marketing/pas/index?from=1788714000&to=1788800399&page=1",
        "campaign_rows": [],
        "overview": None,
        "pagination": {"page": None, "total_pages": None, "has_next": True},
    }
    ready = _with_pagination(
        _synthetic_page(), page="1", total_pages="1", has_next=False
    )
    ready["page_url"] = empty["page_url"]
    responses = [
        _ok_response({}),
        _ok_response({"data": {"result": json.dumps(empty)}}),
        _ok_response({"data": {"result": json.dumps(ready)}}),
    ]
    sleeps: list[float] = []

    def runner(command, **kwargs):
        return responses.pop(0)

    settings = _settings(
        tmp_path,
        page_ready_retries=2,
        page_ready_retry_delay_seconds=1,
    )
    reader = ZiniaoShopeeAdsReader(
        ZiniaoCliClient(runner=runner, settings=settings), sleeper=sleeps.append
    )

    result = reader.read_page("store-1", DAY, 1)

    assert result["campaign_rows"]
    assert sleeps == [1]


def test_ziniao_reader_waits_for_first_page_overview_after_rows_render(tmp_path: Path):
    rows_only = _with_pagination(
        _synthetic_page(), page="1", total_pages="1", has_next=False
    )
    rows_only["overview"] = None
    rows_only["page_url"] = (
        "https://seller.shopee.co.th/portal/marketing/pas/index"
        "?from=1788714000&to=1788800399&page=1"
    )
    ready = deepcopy(rows_only)
    ready["overview"] = deepcopy(_synthetic_page()["overview"])
    responses = [
        _ok_response({}),
        _ok_response({"data": {"result": json.dumps(rows_only)}}),
        _ok_response({"data": {"result": json.dumps(ready)}}),
    ]
    sleeps: list[float] = []

    def runner(command, **kwargs):
        return responses.pop(0)

    settings = _settings(
        tmp_path,
        page_ready_retries=2,
        page_ready_retry_delay_seconds=1,
    )
    reader = ZiniaoShopeeAdsReader(
        ZiniaoCliClient(runner=runner, settings=settings), sleeper=sleeps.append
    )

    result = reader.read_page("store-1", DAY, 1)

    assert result["overview"] == ready["overview"]
    assert sleeps == [1]


def test_ziniao_reader_merges_shop_gmv_max_aggregate_into_first_page(tmp_path: Path):
    standard = _with_pagination(
        _synthetic_page(), page="1", total_pages="1", has_next=False
    )
    standard["has_shop_gmv_max"] = True
    standard["ads_list_view"] = "individual_ads"
    standard["page_url"] = (
        "https://seller.shopee.co.th/portal/marketing/pas/index"
        "?from=1788714000&to=1788800399&page=1"
    )
    shop_row = {
        "item_id": "__SHOP_GMV_MAX__",
        "name": "Shop GMV Max",
        "type": "shop_gmv_max",
        "status": "Ongoing",
        "currency": "THB",
        "spend": "350.00",
        "gmv": "1342.00",
        "orders": "14",
        "reported_roas": "3.83",
    }
    responses = [
        _ok_response({}),
        _ok_response({"data": {"result": json.dumps(standard)}}),
        _ok_response({}),
        _ok_response(
            {
                "data": {
                    "result": json.dumps(
                        {"campaign_row": shop_row, "missing_fields": []}
                    )
                }
            }
        ),
        _ok_response({}),
        _ok_response({"data": {"result": json.dumps(standard)}}),
    ]
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return responses.pop(0)

    reader = ZiniaoShopeeAdsReader(
        ZiniaoCliClient(runner=runner, settings=_settings(tmp_path))
    )

    result = reader.read_page("store-1", DAY, 1)

    assert result["campaign_rows"][-1] == shop_row
    clicks = [command for command in calls if command[1:3] == ["page", "click"]]
    assert [command[-1] for command in clicks] == [
        ".ads-list-tabs .eds-tabs__nav-tab:nth-child(2)",
        ".ads-list-tabs .eds-tabs__nav-tab:nth-child(1)",
    ]
    assert sum(command[1:3] == ["page", "exec"] for command in calls) == 3


@pytest.mark.parametrize(
    "page_url",
    [
        "http://seller.shopee.co.th/portal/marketing/pas/index?from=1788714000&to=1788800399&page=1",
        "https://indexevil.shopee.co.th/portal/marketing/pas/index?from=1788714000&to=1788800399&page=1",
        "https://seller.shopee.co.th/portal/marketing/pas/index?from=1788627600&to=1788713999&page=1",
        "https://seller.shopee.co.th/portal/marketing/pas/index?from=1788714000&to=1788800399&page=2",
        "https://seller.shopee.co.th/portal/marketing/pas/index?from=1788714000&to=1788800399",
    ],
)
def test_ziniao_reader_rejects_page_contract_url_outside_requested_page(
    tmp_path: Path, page_url: str,
):
    page = _synthetic_page()
    page["page_url"] = page_url
    responses = [
        _ok_response({}),
        _ok_response({"data": {"result": json.dumps(page)}}),
    ]

    def runner(command, **kwargs):
        return responses.pop(0)

    reader = ZiniaoShopeeAdsReader(ZiniaoCliClient(runner=runner, settings=_settings(tmp_path)))

    with pytest.raises(ValueError, match="page_url"):
        reader.read_page("store-1", DAY, 1)


def test_reported_roas_and_cpa_are_diagnostics_for_overview_and_campaign(tmp_path: Path):
    page = _synthetic_page()
    page["overview"].update({"spend": "0", "gmv": "5", "orders": "0", "roas": "2", "cpa": "1"})
    page["campaign_rows"][0].update({"spend": "0", "gmv": "5", "orders": "0", "roas": "2", "cpa": "1"})

    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)

    assert result.quality.status is DataQualityStatus.DEGRADED
    assert result.diagnostics.count("reported_roas:mismatch") >= 2
    assert result.diagnostics.count("reported_cpa:mismatch") >= 2

    page["overview"].update({"roas": "0", "cpa": "0"})
    page["campaign_rows"][0].update({"roas": "0", "cpa": "0"})
    clean_zero_denominator = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)
    assert "reported_roas:mismatch" not in clean_zero_denominator.diagnostics
    assert "reported_cpa:mismatch" not in clean_zero_denominator.diagnostics

    page["overview"]["cpa"] = ""
    page["campaign_rows"][0]["cpa"] = ""
    empty_reported_cpa = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)
    assert "reported_cpa:mismatch" not in empty_reported_cpa.diagnostics


@pytest.mark.parametrize("value", ["abc123", "1,23.45", "(1.00)", "12%", "฿฿1.00"])
def test_numeric_fields_reject_embedded_or_malformed_text(tmp_path: Path, value: str):
    page = _synthetic_page()
    page["campaign_rows"][0]["spend"] = value
    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)
    assert "metric.spend" in result.quality.missing_fields
    assert result.quality.status is DataQualityStatus.DEGRADED


@pytest.mark.parametrize("value", ["abc", "1,23.45", "(2)", "12%", "1.2.3"])
def test_invalid_reported_values_are_diagnosed_without_substring_parsing(tmp_path: Path, value: str):
    page = _synthetic_page()
    page["overview"]["roas"] = value
    result = ShopeeAdCollector(_settings(tmp_path), FakePageReader(page)).collect(DAY)
    assert "reported_roas:invalid" in result.diagnostics


def _with_pagination(
    payload: dict[str, object], *, page: str, total_pages: str, has_next: bool, malformed: bool = False,
) -> dict[str, object]:
    if malformed:
        payload["pagination"] = "not-a-pagination-object"
    else:
        payload["pagination"] = {"page": page, "total_pages": total_pages, "has_next": has_next}
    return payload


def _invalid_contract_page() -> dict[str, object]:
    page = _synthetic_page()
    page["captured_at"] = "not-an-iso-time"
    return page


def _without_pagination(page: dict[str, object]) -> dict[str, object]:
    page.pop("pagination")
    return page


def _distinct_page() -> dict[str, object]:
    page = _synthetic_page()
    page["campaign_rows"][0].update({"page_campaign_id": "campaign-2", "item_id": "item-4"})
    page["product_rows"][0].update({"campaign_id": "campaign-2", "item_id": "item-4"})
    page["overview"] = None
    return page


def _ok_response(data: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ziniao-cli"], 0, json.dumps({"ok": True, "data": data}), "")

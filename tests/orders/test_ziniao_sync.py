from datetime import UTC, date, datetime

import pytest

from adwatch.orders.repository import OrderRepository
from adwatch.orders.ziniao_sync import (
    OrderSyncError,
    ShopeeOrderSync,
    ZiniaoShopeeReadClient,
)
from adwatch.storage.db import Database


class FakeReader:
    def __init__(self, *, fail_orders=False, status="Completed"):
        self.calls = []
        self.fail_orders = fail_orders
        self.status = status

    def product_pages(self):
        self.calls.append("products")
        return (
            {
                "data": {
                    "products": [
                        {
                            "id": 123,
                            "name": "Product A",
                            "model_list": [
                                {
                                    "id": 456,
                                    "name": "Blue",
                                    "sku": "SELLER-A",
                                    "stock_detail": {
                                        "total_available_stock": 8
                                    },
                                }
                            ],
                        }
                    ]
                }
            },
        )

    def order_pages(self, *, start):
        self.calls.append("orders")
        if self.fail_orders:
            raise RuntimeError("order page unavailable")
        return (
            (
                {
                    "order_card": {
                        "card_header": {"order_sn": "260801ABC123"},
                        "payment_info": {"total_price": 10000000},
                        "status_info": {"status": self.status},
                        "order_ext_info": {
                            "order_id": 999,
                            "logistics_status": 5,
                        },
                        "item_info_group": {
                            "item_info_list": [
                                {
                                    "item_list": [
                                        {
                                            "name": "Product A",
                                            "description": "Variation: Blue",
                                            "amount": 1,
                                            "inner_item_ext_info": {
                                                "item_id": 123,
                                                "model_id": 456,
                                            },
                                        }
                                    ]
                                }
                            ]
                        },
                    }
                },
            ),
        )


class PartiallyRejectedReader(FakeReader):
    def product_pages(self):
        payload = next(iter(super().product_pages()))
        payload["data"]["products"].append(
            {
                "id": 789,
                "name": "Product without SKU",
                "model_list": [
                    {
                        "id": 101,
                        "name": "Default",
                        "sku": "",
                        "stock_detail": {"total_available_stock": 1},
                    }
                ],
            }
        )
        return (payload,)


class OrderRejectedReader(FakeReader):
    def order_pages(self, *, start):
        pages = super().order_pages(start=start)
        item = pages[0][0]["order_card"]["item_info_group"]["item_info_list"][0]
        item["item_list"][0]["inner_item_ext_info"] = {
            "item_id": 404,
            "model_id": 505,
        }
        return pages


def test_sync_products_before_orders_and_persists_run(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    reader = FakeReader()
    service = ShopeeOrderSync(
        OrderRepository(database),
        reader,
        store="虾皮泰国",
        clock=lambda: datetime(2026, 8, 4, 9, tzinfo=UTC),
    )

    result = service.sync(
        start=date(2026, 8, 1),
        end=date(2026, 8, 1),
    )

    assert reader.calls == ["products", "orders"]
    assert result.status == "success"
    assert result.sku_count == 1
    assert result.order_count == 1
    assert result.refund_count == 0
    assert result.logistics_count == 1
    assert result.inventory_snapshot_count == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM platform_order_lines"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM order_sync_runs"
        ).fetchone()[0] == "success"
        run = connection.execute(
            """
            SELECT start_date, end_date, refund_count, logistics_count,
                   inventory_snapshot_count
            FROM order_sync_runs
            """
        ).fetchone()
        assert tuple(run) == (
            "2026-08-01",
            "2026-08-01",
            0,
            1,
            1,
        )


def test_sync_persists_refund_observation_and_is_idempotent(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    service = ShopeeOrderSync(
        OrderRepository(database),
        FakeReader(status="Return/Refund"),
        store="虾皮泰国",
        clock=lambda: datetime(2026, 8, 4, 9, tzinfo=UTC),
    )

    first = service.sync(
        start=date(2026, 8, 1),
        end=date(2026, 8, 1),
    )
    second = service.sync(
        start=date(2026, 8, 1),
        end=date(2026, 8, 1),
    )

    assert first.refund_count == 1
    assert second.refund_count == 0
    assert second.logistics_count == 0
    assert second.inventory_snapshot_count == 0
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM platform_refund_observations"
        ).fetchone()[0] == 1


def test_sync_uses_business_timezone_for_observation_day(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    service = ShopeeOrderSync(
        OrderRepository(database),
        FakeReader(status="Return/Refund"),
        store="虾皮泰国",
        timezone="Asia/Shanghai",
        clock=lambda: datetime(2026, 8, 3, 17, tzinfo=UTC),
    )

    service.sync(start=date(2026, 8, 1), end=date(2026, 8, 1))

    with database.connect() as connection:
        refund_day = connection.execute(
            "SELECT observed_on FROM platform_refund_observations"
        ).fetchone()[0]
        snapshot_day = connection.execute(
            "SELECT snapshot_date FROM platform_inventory_snapshots"
        ).fetchone()[0]
    assert refund_day == "2026-08-04"
    assert snapshot_day == "2026-08-04"


def test_sync_failure_records_run_without_deleting_old_orders(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    successful = ShopeeOrderSync(
        repository,
        FakeReader(),
        store="虾皮泰国",
        clock=lambda: datetime(2026, 8, 4, 9, tzinfo=UTC),
    )
    successful.sync(start=date(2026, 8, 1), end=date(2026, 8, 1))
    failing = ShopeeOrderSync(
        repository,
        FakeReader(fail_orders=True),
        store="虾皮泰国",
        clock=lambda: datetime(2026, 8, 4, 10, tzinfo=UTC),
    )

    with pytest.raises(OrderSyncError):
        failing.sync(start=date(2026, 8, 1), end=date(2026, 8, 1))

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM platform_order_lines"
        ).fetchone()[0] == 1
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM order_sync_runs ORDER BY started_at"
            )
        ]
    assert statuses == ["success", "failed"]


def test_product_without_seller_sku_is_a_nonblocking_sync_warning(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    result = ShopeeOrderSync(
        OrderRepository(database),
        PartiallyRejectedReader(),
        store="虾皮泰国",
        clock=lambda: datetime(2026, 8, 4, 9, tzinfo=UTC),
    ).sync(start=date(2026, 8, 1), end=date(2026, 8, 1))

    with database.connect() as connection:
        run = connection.execute(
            """
            SELECT status, rejected_count, product_rejected_count,
                   order_rejected_count, error_message
            FROM order_sync_runs
            """
        ).fetchone()

    assert result.status == "success"
    assert result.rejected_count == 1
    assert result.product_rejected_count == 1
    assert result.order_rejected_count == 0
    assert tuple(run[:4]) == ("success", 1, 1, 0)
    assert "has no Seller SKU" in run["error_message"]


def test_unmapped_order_item_keeps_sync_partial(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()

    result = ShopeeOrderSync(
        OrderRepository(database),
        OrderRejectedReader(),
        store="虾皮泰国",
        clock=lambda: datetime(2026, 8, 4, 9, tzinfo=UTC),
    ).sync(start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.status == "partial"
    assert result.product_rejected_count == 0
    assert result.order_rejected_count == 1


def test_ziniao_reader_uses_navigation_and_exec_only_for_products():
    class FakeCli:
        def __init__(self):
            self.calls = []

        def navigate_and_exec(self, store_id, url, script, **kwargs):
            self.calls.append(("navigate_and_exec", store_id, url, script))
            return True

        def page_exec(self, store_id, script):
            self.calls.append(("page_exec", store_id, script))
            return "started"

        def page_exec_until(self, store_id, script, *, ready, attempts):
            self.calls.append(("page_exec_until", store_id, script))
            value = {
                "ready": True,
                "data": {
                    "code": 0,
                    "data": {
                        "products": [{"id": 1}],
                        "page_info": {"total": 1, "cursor": "done"},
                    },
                },
            }
            assert ready(value)
            return value

    client = FakeCli()
    pages = tuple(ZiniaoShopeeReadClient(client, "222").product_pages())

    assert pages[0]["data"]["products"] == [{"id": 1}]
    assert [call[0] for call in client.calls] == [
        "navigate_and_exec",
        "page_exec",
        "page_exec_until",
    ]

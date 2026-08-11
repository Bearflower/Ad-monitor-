from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.models import PlatformOrderLine
from adwatch.orders.repository import OrderRepository
from adwatch.orders.sync import OperationsSyncService
from adwatch.storage.db import Database


def _order(
    order_id: str,
    *,
    quantity: int = 2,
    order_status: str = "completed",
    logistics_status: str = "delivered",
    refund_status: str = "",
    ordered_on: date = date(2026, 7, 10),
) -> PlatformOrderLine:
    return PlatformOrderLine(
        "shopee",
        "shop",
        order_id,
        f"item-{order_id}",
        "model-1",
        "SKU-1",
        "1 bag",
        "Product",
        quantity,
        Decimal(20),
        "THB",
        order_status,
        logistics_status,
        refund_status,
        ordered_on,
        datetime(2026, 7, 11, tzinfo=UTC),
    )


def _set_policy(database, mode: str) -> None:
    FulfillmentService(database).set_policy(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        mode=mode,
        supply_status="available",
    )


def test_sync_freezes_historical_cost_and_is_idempotent(tmp_path):
    database = Database(tmp_path / "sync.sqlite3")
    database.migrate()
    _set_policy(database, "stocked")
    orders = OrderRepository(database)
    inventory = InventoryService(database)
    inventory.receive_purchase(
        receipt_id="PO-1",
        supplier="factory",
        received_on=date(2026, 7, 1),
        lines=(PurchaseLine("SKU-1", 10, Decimal(4)),),
        actor="yl",
    )
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 20),
        unit_cost_cny=Decimal(7),
    )
    orders.upsert_orders((_order("ORDER-1"),))

    service = OperationsSyncService(database)
    first = service.sync()
    second = service.sync()

    assert first.shipped == 1
    assert second.shipped == 0
    assert second.unchanged == 1
    assert inventory.balance("SKU-1") == 8
    with database.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT quantity, unit_cost_cny, total_cost_cny, cost_effective_date
            FROM order_cost_snapshots WHERE order_id='ORDER-1'
            """
        ).fetchone()
    assert dict(snapshot) == {
        "quantity": 2,
        "unit_cost_cny": "5",
        "total_cost_cny": "10",
        "cost_effective_date": "2026-07-01",
    }


def test_sync_reports_missing_cost_and_does_not_ship_cancelled_order(tmp_path):
    database = Database(tmp_path / "sync.sqlite3")
    database.migrate()
    _set_policy(database, "stocked")
    orders = OrderRepository(database)
    orders.upsert_orders(
        (
            _order("MISSING"),
            _order(
                "CANCELLED",
                order_status="cancelled",
                logistics_status="cancelled",
            ),
        )
    )

    result = OperationsSyncService(database).sync()

    assert result.pending_cost == 1
    assert result.cancelled == 1
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0]
            == 0
        )


def test_sync_return_restocks_and_marks_cost_snapshot_returned(tmp_path):
    database = Database(tmp_path / "sync.sqlite3")
    database.migrate()
    _set_policy(database, "stocked")
    orders = OrderRepository(database)
    inventory = InventoryService(database)
    inventory.receive_purchase(
        receipt_id="PO-1",
        supplier="factory",
        received_on=date(2026, 7, 1),
        lines=(PurchaseLine("SKU-1", 10, Decimal(5)),),
        actor="yl",
    )
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders((_order("RETURN-1"),))
    service = OperationsSyncService(database)
    service.sync()
    orders.upsert_orders((_order("RETURN-1", refund_status="returned"),))

    result = service.sync()

    assert result.returned == 1
    assert inventory.balance("SKU-1") == 10
    with database.connect() as connection:
        status = connection.execute(
            """
            SELECT status FROM order_cost_snapshots
            WHERE order_id='RETURN-1'
            """
        ).fetchone()[0]
    assert status == "returned"


def test_return_without_prior_shipment_does_not_create_stock(tmp_path):
    database = Database(tmp_path / "sync.sqlite3")
    database.migrate()
    _set_policy(database, "stocked")
    OrderRepository(database).upsert_orders(
        (_order("RETURN-NOT-SHIPPED", refund_status="returned"),)
    )

    result = OperationsSyncService(database).sync()

    assert result.returned == 0
    assert result.unchanged == 1
    assert InventoryService(database).balance("SKU-1") == 0


def test_supplier_fulfilled_order_costs_without_inventory(tmp_path):
    database = Database(tmp_path / "sync.sqlite3")
    database.migrate()
    _set_policy(database, "supplier_fulfilled")
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders((_order("SUPPLIER-1"),))

    first = OperationsSyncService(database).sync()
    second = OperationsSyncService(database).sync()

    assert first.supplier_costed == 1
    assert first.pending_inventory == 0
    assert second.unchanged == 1
    with database.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT total_cost_cny, status FROM order_cost_snapshots
            WHERE order_id='SUPPLIER-1'
            """
        ).fetchone()
        movements = connection.execute(
            "SELECT COUNT(*) FROM inventory_movements"
        ).fetchone()[0]
    assert tuple(snapshot) == ("10", "confirmed")
    assert movements == 0


def test_supplier_sync_tops_up_matching_partial_cost_snapshot(tmp_path):
    database = Database(tmp_path / "partial-supplier-cost.sqlite3")
    database.migrate()
    _set_policy(database, "supplier_fulfilled")
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders((_order("PARTIAL-SUPPLIER", quantity=3),))
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','PARTIAL-SUPPLIER','SKU-1',2,'5','10',
              '2026-07-01','confirmed','2026-07-10T00:00:00Z')
            """
        )

    result = OperationsSyncService(database).sync()

    assert result.supplier_costed == 1
    with database.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT quantity, unit_cost_cny, total_cost_cny, cost_effective_date
            FROM order_cost_snapshots WHERE order_id='PARTIAL-SUPPLIER'
            """
        ).fetchone()
    assert tuple(snapshot) == (3, "5", "15", "2026-07-01")


def test_supplier_sync_keeps_partial_snapshot_when_cost_version_differs(tmp_path):
    database = Database(tmp_path / "partial-cost-mismatch.sqlite3")
    database.migrate()
    _set_policy(database, "supplier_fulfilled")
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders((_order("PARTIAL-MISMATCH", quantity=3),))
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','PARTIAL-MISMATCH','SKU-1',2,'4','8',
              '2026-07-01','confirmed','2026-07-10T00:00:00Z')
            """
        )

    result = OperationsSyncService(database).sync()

    assert result.supplier_costed == 0
    assert result.unchanged == 1
    with database.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT quantity, unit_cost_cny, total_cost_cny
            FROM order_cost_snapshots WHERE order_id='PARTIAL-MISMATCH'
            """
        ).fetchone()
    assert tuple(snapshot) == (2, "4", "8")


def test_sync_uses_resolved_order_line_once_when_legacy_placeholder_exists(
    tmp_path,
):
    database = Database(tmp_path / "dedup-sync.sqlite3")
    database.migrate()
    _set_policy(database, "supplier_fulfilled")
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    resolved = _order("ORDER-DUPLICATE", quantity=1)
    legacy = replace(resolved, item_id="SKU-1", model_id="SKU-1")
    orders.upsert_orders((legacy, resolved))

    result = OperationsSyncService(database).sync()

    assert result.supplier_costed == 1
    with database.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT quantity, unit_cost_cny, total_cost_cny
            FROM order_cost_snapshots WHERE order_id='ORDER-DUPLICATE'
            """
        ).fetchone()
    assert tuple(snapshot) == (1, "5", "5")


def test_sync_keeps_multiple_resolved_order_lines_pending_for_review(tmp_path):
    database = Database(tmp_path / "ambiguous-sync.sqlite3")
    database.migrate()
    _set_policy(database, "supplier_fulfilled")
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    first = _order("ORDER-AMBIGUOUS", quantity=1)
    second = replace(first, item_id="item-second", model_id="model-second")
    orders.upsert_orders((first, second))

    result = OperationsSyncService(database).sync()

    assert result.pending_order_identity == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_cost_snapshots"
        ).fetchone()[0] == 0


def test_missing_fulfillment_policy_is_explicitly_pending(tmp_path):
    database = Database(tmp_path / "sync.sqlite3")
    database.migrate()
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders((_order("NO-POLICY"),))

    result = OperationsSyncService(database).sync()

    assert result.pending_fulfillment == 1
    assert result.pending_inventory == 0


def test_store_default_costs_supplier_order_before_shipped_status(tmp_path):
    database = Database(tmp_path / "default-sync.sqlite3")
    database.migrate()
    FulfillmentService(database).set_store_default(
        platform="shopee",
        store="shop",
        effective_date=date(2026, 7, 1),
        mode="supplier_fulfilled",
        supply_status="available",
    )
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders(
        (_order("SUPPLIER-PENDING", order_status="Order Received", logistics_status=""),)
    )

    result = OperationsSyncService(database).sync()

    assert result.supplier_costed == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_cost_snapshots"
        ).fetchone()[0] == 1


def test_sku_stocked_override_beats_store_default(tmp_path):
    database = Database(tmp_path / "default-override.sqlite3")
    database.migrate()
    fulfillment = FulfillmentService(database)
    fulfillment.set_store_default(
        platform="shopee",
        store="shop",
        effective_date=date(2026, 7, 1),
        mode="supplier_fulfilled",
        supply_status="available",
    )
    fulfillment.set_policy(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 15),
        mode="stocked",
        supply_status="available",
    )
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders(
        (
            _order(
                "STOCKED-OVERRIDE",
                order_status="Order Received",
                logistics_status="",
                ordered_on=date(2026, 7, 20),
            ),
        )
    )

    result = OperationsSyncService(database).sync()

    assert result.pending_inventory == 0
    assert result.unchanged == 1
    assert InventoryService(database).balance("SKU-1") == 0


def test_sync_resolves_cost_and_fulfillment_through_store_alias(tmp_path):
    database = Database(tmp_path / "alias-sync.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'source-shop', 'shop')
            """
        )
    FulfillmentService(database).set_policy(
        platform="shopee",
        store="source-shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        mode="supplier_fulfilled",
        supply_status="available",
    )
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="source-shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders((_order("ALIAS-ORDER"),))

    result = OperationsSyncService(database).sync()

    assert result.supplier_costed == 1
    assert result.pending_cost == 0
    assert result.pending_fulfillment == 0
    with database.connect() as connection:
        cost = connection.execute(
            """
            SELECT store, unit_cost_cny, cost_effective_date
            FROM order_cost_snapshots WHERE order_id='ALIAS-ORDER'
            """
        ).fetchone()
        fulfillment = connection.execute(
            """
            SELECT store, mode, policy_effective_date
            FROM order_fulfillment_snapshots WHERE order_id='ALIAS-ORDER'
            """
        ).fetchone()
    assert tuple(cost) == ("shop", "5", "2026-07-01")
    assert tuple(fulfillment) == (
        "shop",
        "supplier_fulfilled",
        "2026-07-01",
    )


def test_sync_does_not_duplicate_cost_snapshot_across_store_aliases(tmp_path):
    database = Database(tmp_path / "alias-snapshot-idempotency.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'source-shop', 'shop')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','source-shop','ALIAS-ORDER','SKU-1',2,'5','10',
              '2026-07-01','confirmed','2026-07-10T00:00:00Z')
            """
        )
    _set_policy(database, "supplier_fulfilled")
    orders = OrderRepository(database)
    orders.set_sku_cost(
        platform="shopee",
        store="shop",
        seller_sku="SKU-1",
        effective_date=date(2026, 7, 1),
        unit_cost_cny=Decimal(5),
    )
    orders.upsert_orders((_order("ALIAS-ORDER"),))

    result = OperationsSyncService(database).sync()

    assert result.supplier_costed == 0
    assert result.unchanged == 1
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM order_cost_snapshots "
            "WHERE order_id='ALIAS-ORDER'"
        ).fetchone()[0]
    assert count == 1

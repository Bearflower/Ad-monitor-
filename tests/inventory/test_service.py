from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from adwatch.inventory.models import PurchaseLine
from adwatch.inventory.service import InventoryError, InventoryService
from adwatch.orders.models import PlatformOrderLine, PlatformSku
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


@pytest.fixture
def inventory(tmp_path):
    database = Database(tmp_path / "inventory.sqlite3")
    database.migrate()
    return InventoryService(database), database


def test_purchase_sale_return_and_damage_preserve_inventory_equation(inventory):
    service, _ = inventory
    service.receive_purchase(
        receipt_id="PO-1",
        supplier="工厂",
        received_on=date(2026, 7, 1),
        lines=(PurchaseLine("SKU-1", 10, Decimal(5)),),
        actor="yl",
    )
    service.ship_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=3,
        shipped_on=date(2026, 7, 2),
        unit_cost_cny=Decimal(5),
    )
    service.return_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=1,
        returned_on=date(2026, 7, 3),
    )
    service.damage(
        seller_sku="SKU-1",
        quantity=2,
        occurred_on=date(2026, 7, 4),
        reason="运输破损",
        actor="yl",
    )

    assert service.balance("SKU-1") == 6
    assert service.ship_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=3,
        shipped_on=date(2026, 7, 2),
        unit_cost_cny=Decimal(5),
    ) is False


def test_platform_inventory_read_model_is_separate_from_manual_ledger(tmp_path):
    database = Database(tmp_path / "platform-inventory.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed = datetime(2026, 8, 3, 9, tzinfo=UTC)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "shop", "item-1", "model-1", "SKU-1", "",
                "Product 1", 10, observed,
            ),
            PlatformSku(
                "shopee", "shop", "item-2", "model-2", "SKU-2", "",
                "Product 2", 5, observed,
            ),
        )
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO platform_inventory_snapshots(
                platform, store, item_id, model_id, seller_sku,
                snapshot_date, available_units, observed_at, source
            ) VALUES ('shopee', 'shop', 'item-1', 'model-1', 'SKU-1',
                      '2026-08-03', 10, ?, 'test')
            """,
            (observed.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO platform_inventory_snapshots(
                platform, store, item_id, model_id, seller_sku,
                snapshot_date, available_units, observed_at, source
            ) VALUES ('shopee', 'shop', 'item-2', 'model-2', 'SKU-2',
                      '2026-08-03', 5, ?, 'test')
            """,
            (observed.isoformat(),),
        )

    service = InventoryService(database)
    assert service.platform_sku_count() == 2
    assert service.latest_platform_inventory() == {
        "snapshot_date": "2026-08-03",
        "sku_count": 2,
        "available_units": 15,
    }
    assert service.list_inventory_balances() == ()


def test_order_cost_gaps_are_reported_separately_from_sku_price_gaps(tmp_path):
    database = Database(tmp_path / "order-cost-gaps.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed = datetime(2026, 8, 3, 9, tzinfo=UTC)
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "shop", "ORDER-1", "item-1", "model-1",
                "SKU-1", "", "Product 1", 2, Decimal(100), "THB",
                "completed", "delivered", "", date(2026, 8, 1), observed,
            ),
            PlatformOrderLine(
                "shopee", "shop", "ORDER-2", "item-2", "model-2",
                "SKU-2", "", "Product 2", 1, Decimal(80), "THB",
                "completed", "delivered", "", date(2026, 8, 1), observed,
            ),
        )
    )
    InventoryService(database).record_order_cost(
        platform="shopee",
        store="shop",
        order_id="ORDER-2",
        seller_sku="SKU-2",
        quantity=1,
        unit_cost_cny=Decimal(3),
        cost_effective_date=date(2026, 8, 1),
    )

    gaps = InventoryService(database).list_order_cost_gaps()
    assert gaps == (
        {
            "order_date": "2026-08-01",
            "platform": "shopee",
            "store": "shop",
            "order_id": "ORDER-1",
            "seller_sku": "SKU-1",
            "missing_units": 2,
        },
    )


def test_frozen_order_cost_snapshot_closes_sku_price_gap(tmp_path):
    database = Database(tmp_path / "snapshot-closes-price-gap.sqlite3")
    database.migrate()
    observed = datetime(2026, 8, 3, 9, tzinfo=UTC)
    OrderRepository(database).upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "shop", "ORDER-FROZEN", "item", "model",
                "SKU-FROZEN", "", "Frozen cost product", 2, Decimal(100),
                "THB", "completed", "delivered", "", date(2026, 8, 1),
                observed,
            ),
        )
    )
    InventoryService(database).record_order_cost(
        platform="shopee",
        store="shop",
        order_id="ORDER-FROZEN",
        seller_sku="SKU-FROZEN",
        quantity=2,
        unit_cost_cny=Decimal(5),
        cost_effective_date=date(2026, 7, 1),
    )

    service = InventoryService(database)
    assert service.list_missing_sku_costs() == ()
    assert service.list_order_cost_gaps() == ()


def test_distinct_business_identity_snapshot_closes_only_its_price_gap(tmp_path):
    database = Database(tmp_path / "identity-snapshot-gap.sqlite3")
    database.migrate()
    observed = datetime(2026, 8, 10, 9, tzinfo=UTC)
    repository = OrderRepository(database)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "shop", "item-50", "model-50", "Chief",
                "Chief 50ml", "Roll On", 1, observed,
            ),
            PlatformSku(
                "shopee", "shop", "item-250", "model-250", "Chief",
                "Chief 250ml", "Body Spray", 1, observed,
            ),
        )
    )
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "shop", "ORDER-50", "item-50", "model-50",
                "Chief", "Chief 50ml", "Roll On", 1, Decimal(100), "THB",
                "completed", "delivered", "", date(2026, 8, 10), observed,
            ),
        )
    )
    InventoryService(database).record_order_cost(
        platform="shopee",
        store="shop",
        order_id="ORDER-50",
        seller_sku="Chief [item-50:model-50]",
        quantity=1,
        unit_cost_cny=Decimal(5),
        cost_effective_date=date(2026, 8, 1),
    )

    service = InventoryService(database)
    assert service.list_missing_sku_costs() == ()
    assert service.list_order_cost_gaps() == ()


def test_legacy_snapshot_is_not_rekeyed_when_order_has_two_business_identities(
    tmp_path,
):
    database = Database(tmp_path / "ambiguous-legacy-snapshot.sqlite3")
    database.migrate()
    observed = datetime(2026, 8, 10, 9, tzinfo=UTC)
    repository = OrderRepository(database)
    repository.upsert_skus(
        (
            PlatformSku(
                "shopee", "shop", "item-50", "model-50", "Chief",
                "Chief 50ml", "Roll On", 1, observed,
            ),
            PlatformSku(
                "shopee", "shop", "item-250", "model-250", "Chief",
                "Chief 250ml", "Body Spray", 1, observed,
            ),
        )
    )
    first = PlatformOrderLine(
        "shopee", "shop", "ORDER-TWO", "item-50", "model-50", "Chief",
        "Chief 50ml", "Roll On", 1, Decimal(100), "THB", "completed",
        "delivered", "", date(2026, 8, 10), observed,
    )
    repository.upsert_orders(
        (
            first,
            replace(
                first,
                item_id="item-250",
                model_id="model-250",
                variation_name="Chief 250ml",
                product_name="Body Spray",
            ),
        )
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','ORDER-TWO','Chief',1,'5','5',
              '2026-08-01','confirmed','2026-08-10T00:00:00Z')
            """
        )

    changed = InventoryService(database).record_order_cost(
        platform="shopee",
        store="shop",
        order_id="ORDER-TWO",
        seller_sku="Chief [item-50:model-50]",
        legacy_seller_sku="Chief",
        quantity=1,
        unit_cost_cny=Decimal(5),
        cost_effective_date=date(2026, 8, 1),
    )

    assert changed is False
    with database.connect() as connection:
        keys = {
            row[0]
            for row in connection.execute(
                """
                SELECT seller_sku FROM order_cost_snapshots
                WHERE order_id='ORDER-TWO'
                """
            )
        }
    assert keys == {"Chief"}


def test_order_cost_gap_resolves_store_alias_and_quantity_coverage(tmp_path):
    database = Database(tmp_path / "order-cost-alias-quantity.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    observed = datetime(2026, 8, 3, 9, tzinfo=UTC)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'source-shop', 'canonical-shop')
            """
        )
    repository.upsert_orders(
        (
            PlatformOrderLine(
                "shopee", "source-shop", "ORDER-ALIAS", "item-1", "model-1",
                "SKU-1", "", "Product 1", 3, Decimal(100), "THB",
                "completed", "delivered", "", date(2026, 8, 1), observed,
            ),
        )
    )
    InventoryService(database).record_order_cost(
        platform="shopee",
        store="canonical-shop",
        order_id="ORDER-ALIAS",
        seller_sku="SKU-1",
        quantity=2,
        unit_cost_cny=Decimal(3),
        cost_effective_date=date(2026, 8, 1),
    )

    gaps = InventoryService(database).list_order_cost_gaps()
    assert gaps[0]["missing_units"] == 1

    with database.transaction() as connection:
        connection.execute(
            "UPDATE order_cost_snapshots SET quantity=3 WHERE order_id='ORDER-ALIAS'"
        )
    assert InventoryService(database).list_order_cost_gaps() == ()


def test_order_cost_gap_ignores_legacy_placeholder_when_resolved_line_exists(
    tmp_path,
):
    database = Database(tmp_path / "duplicate-order-cost-gap.sqlite3")
    database.migrate()
    observed = datetime(2026, 8, 3, 9, tzinfo=UTC)
    resolved = PlatformOrderLine(
        "shopee", "shop", "ORDER-DUPLICATE", "item-1", "model-1",
        "SKU-1", "", "Product 1", 1, Decimal(100), "THB",
        "completed", "delivered", "", date(2026, 8, 1), observed,
    )
    legacy = replace(resolved, item_id="SKU-1", model_id="SKU-1")
    OrderRepository(database).upsert_orders((legacy, resolved))
    InventoryService(database).record_order_cost(
        platform="shopee",
        store="shop",
        order_id="ORDER-DUPLICATE",
        seller_sku="SKU-1",
        quantity=1,
        unit_cost_cny=Decimal(3),
        cost_effective_date=date(2026, 8, 1),
    )

    assert InventoryService(database).list_order_cost_gaps() == ()


def test_returned_snapshot_is_not_reported_when_alias_feed_omits_refund(tmp_path):
    database = Database(tmp_path / "sticky-return-gap.sqlite3")
    database.migrate()
    observed = datetime(2026, 8, 10, 9, tzinfo=UTC)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES ('shopee', 'legacy-shop', 'shop')
            """
        )
    base = PlatformOrderLine(
        "shopee", "legacy-shop", "RETURNED-ORDER", "legacy-item",
        "legacy-model", "SKU-RETURNED", "", "Returned product", 1,
        Decimal(100), "THB", "completed", "delivered", "returned",
        date(2026, 7, 22), observed,
    )
    current = replace(
        base,
        store="shop",
        item_id="current-item",
        model_id="current-model",
        refund_status="",
        source_updated_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
    )
    OrderRepository(database).upsert_orders((base, current))
    InventoryService(database).record_order_cost(
        platform="shopee",
        store="legacy-shop",
        order_id="RETURNED-ORDER",
        seller_sku="SKU-RETURNED",
        quantity=1,
        unit_cost_cny=Decimal(3),
        cost_effective_date=date(2026, 7, 1),
        status="returned",
    )

    assert InventoryService(database).list_order_cost_gaps() == ()


def test_latest_platform_inventory_uses_latest_fact_per_store_and_sku(tmp_path):
    database = Database(tmp_path / "latest-platform-inventory.sqlite3")
    database.migrate()
    observed_old = datetime(2026, 8, 1, 9, tzinfo=UTC)
    observed_new = datetime(2026, 8, 2, 9, tzinfo=UTC)
    with database.transaction() as connection:
        for store, item, sku, snapshot_date, units, observed in (
            ("shop-a", "item-1", "SKU-1", "2026-08-01", 10, observed_old),
            ("shop-a", "item-1", "SKU-1", "2026-08-02", 5, observed_new),
            ("shop-b", "item-2", "SKU-2", "2026-08-01", 20, observed_old),
        ):
            connection.execute(
                """
                INSERT INTO platform_inventory_snapshots(
                    platform, store, item_id, model_id, seller_sku,
                    snapshot_date, available_units, observed_at, source
                ) VALUES ('shopee', ?, ?, 'model', ?, ?, ?, ?, 'test')
                """,
                (
                    store,
                    item,
                    sku,
                    snapshot_date,
                    units,
                    observed.isoformat(),
                ),
            )

    assert InventoryService(database).latest_platform_inventory() == {
        "snapshot_date": "2026-08-02",
        "sku_count": 2,
        "available_units": 25,
    }


def test_cancelled_order_does_not_ship_and_negative_stock_is_blocked(inventory):
    service, _ = inventory
    assert service.ship_order(
        platform="shopee",
        store="shop",
        order_id="CANCELLED",
        seller_sku="SKU-1",
        quantity=1,
        shipped_on=date(2026, 7, 2),
        unit_cost_cny=Decimal(5),
        order_status="cancelled",
    ) is False
    with pytest.raises(InventoryError, match="insufficient"):
        service.ship_order(
            platform="shopee",
            store="shop",
            order_id="ORDER-2",
            seller_sku="SKU-1",
            quantity=1,
            shipped_on=date(2026, 7, 2),
            unit_cost_cny=Decimal(5),
        )


def test_manual_adjustment_supports_both_directions_and_single_reversal(inventory):
    service, database = inventory
    positive = service.adjust_inventory(
        seller_sku="SKU-1",
        quantity_delta=5,
        occurred_on=date(2026, 8, 4),
        reason="首次盘点",
        actor="operator",
    )
    negative = service.adjust_inventory(
        seller_sku="SKU-1",
        quantity_delta=-2,
        occurred_on=date(2026, 8, 4),
        reason="盘亏",
        actor="operator",
    )
    reversal = service.reverse_movement(
        negative,
        occurred_on=date(2026, 8, 4),
        reason="盘点录错",
        actor="reviewer",
    )

    assert service.balance("SKU-1") == 5
    with pytest.raises(InventoryError, match="already reversed"):
        service.reverse_movement(
            negative,
            occurred_on=date(2026, 8, 4),
            reason="重复冲销",
            actor="reviewer",
        )
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, actor, reason, reversal_of
            FROM inventory_movements ORDER BY created_at, id
            """
        ).fetchall()
    assert {row["id"] for row in rows} == {positive, negative, reversal}
    reverse_row = next(row for row in rows if row["id"] == reversal)
    assert tuple(reverse_row)[1:] == ("reviewer", "盘点录错", negative)


@pytest.mark.parametrize(
    ("quantity_delta", "reason", "error"),
    [
        (0, "盘点", "non-zero"),
        (1, "", "reason"),
        (-1, "盘亏", "insufficient"),
    ],
)
def test_manual_adjustment_rejects_invalid_inputs(
    inventory, quantity_delta, reason, error
):
    service, _ = inventory

    with pytest.raises(InventoryError, match=error):
        service.adjust_inventory(
            seller_sku="SKU-1",
            quantity_delta=quantity_delta,
            occurred_on=date(2026, 8, 4),
            reason=reason,
            actor="operator",
        )


def test_purchase_reversal_is_atomic_and_requires_available_stock(inventory):
    service, database = inventory
    service.receive_purchase(
        receipt_id="PO-1",
        supplier="工厂",
        received_on=date(2026, 8, 1),
        lines=(PurchaseLine("SKU-1", 10, Decimal(5)),),
        actor="operator",
    )
    service.ship_order(
        platform="shopee",
        store="shop",
        order_id="ORDER-1",
        seller_sku="SKU-1",
        quantity=6,
        shipped_on=date(2026, 8, 2),
        unit_cost_cny=Decimal(5),
    )

    with pytest.raises(InventoryError, match="insufficient"):
        service.reverse_purchase(
            "PO-1",
            occurred_on=date(2026, 8, 4),
            reason="整单录错",
            actor="reviewer",
        )

    with database.connect() as connection:
        receipt = connection.execute(
            "SELECT status, reversal_reason FROM purchase_receipts WHERE id='PO-1'"
        ).fetchone()
        reversals = connection.execute(
            """
            SELECT COUNT(*) FROM inventory_movements
            WHERE movement_type='purchase_reversal'
            """
        ).fetchone()[0]
    assert tuple(receipt) == ("confirmed", None)
    assert reversals == 0


def test_purchase_and_damage_can_reverse_once_with_audit(inventory):
    service, database = inventory
    service.receive_purchase(
        receipt_id="PO-2",
        supplier="工厂",
        received_on=date(2026, 8, 1),
        lines=(PurchaseLine("SKU-1", 5, Decimal(5)),),
        actor="operator",
    )
    damage = service.damage(
        seller_sku="SKU-1",
        quantity=2,
        occurred_on=date(2026, 8, 2),
        reason="破损",
        actor="operator",
    )
    service.reverse_movement(
        damage,
        occurred_on=date(2026, 8, 3),
        reason="误报破损",
        actor="reviewer",
    )
    service.reverse_purchase(
        "PO-2",
        occurred_on=date(2026, 8, 4),
        reason="采购单重复",
        actor="reviewer",
    )

    assert service.balance("SKU-1") == 0
    with pytest.raises(InventoryError, match="only confirmed"):
        service.reverse_purchase(
            "PO-2",
            occurred_on=date(2026, 8, 4),
            reason="再次冲销",
            actor="reviewer",
        )
    with database.connect() as connection:
        receipt = connection.execute(
            """
            SELECT status, created_by, reversal_reason
            FROM purchase_receipts WHERE id='PO-2'
            """
        ).fetchone()
        audits = connection.execute(
            """
            SELECT action FROM audit_events
            WHERE object_id IN ('PO-2', ?) ORDER BY created_at, id
            """,
            (damage,),
        ).fetchall()
    assert tuple(receipt) == ("reversed", "operator", "采购单重复")
    assert "reverse" in {row["action"] for row in audits}

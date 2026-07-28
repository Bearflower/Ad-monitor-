from datetime import datetime

from adwatch.orders.models import PlatformSku
from adwatch.orders.repository import OrderRepository
from adwatch.storage.db import Database


def test_sku_upsert_is_idempotent_and_keeps_latest_inventory(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = OrderRepository(database)
    first = PlatformSku(
        "shopee", "虾皮泰国", "item", "model", "seller-sku", "1 bag",
        "Foot soak", 31, datetime(2026, 7, 28, 9),
    )
    latest = PlatformSku(
        "shopee", "虾皮泰国", "item", "model", "seller-sku", "1 bag",
        "Foot soak", 29, datetime(2026, 7, 29, 9),
    )

    repository.upsert_skus((first,))
    repository.upsert_skus((latest,))

    pending = repository.pending_sku_costs()
    assert len(pending) == 1
    assert pending[0]["inventory_units"] == 29
    assert pending[0]["seller_sku"] == "seller-sku"

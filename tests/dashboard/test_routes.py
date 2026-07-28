from adwatch.dashboard.routes import DashboardRouter
from adwatch.inventory.service import InventoryService
from adwatch.ledger.service import LedgerService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.profit_sharing.service import ProfitSharingService
from adwatch.storage.db import Database


def test_expense_post_requires_csrf_and_uses_ledger_service(tmp_path):
    database = Database(tmp_path / "web.sqlite3")
    database.migrate()
    router = DashboardRouter(LedgerService(database), csrf_token="secret")
    form = {
        "csrf_token": "wrong",
        "occurred_on": "2026-07-28",
        "category": "物流",
        "amount": "100",
        "currency": "CNY",
        "rate_to_cny": "1",
        "payer": "洁云",
        "fund_nature": "operating_expense",
        "affects_profit": "1",
    }

    assert router.post("/expenses", form).status == 403
    form["csrf_token"] = "secret"
    response = router.post("/expenses", form)

    assert response.status == 303
    with database.connect() as connection:
        row = connection.execute(
            "SELECT category, status FROM expense_entries"
        ).fetchone()
    assert dict(row) == {"category": "物流", "status": "draft"}


def test_invalid_decimal_returns_400_without_writing(tmp_path):
    database = Database(tmp_path / "web.sqlite3")
    database.migrate()
    router = DashboardRouter(LedgerService(database), csrf_token="secret")
    response = router.post(
        "/expenses",
        {
            "csrf_token": "secret",
            "occurred_on": "2026-07-28",
            "category": "物流",
            "amount": "not-money",
            "currency": "CNY",
            "rate_to_cny": "1",
            "payer": "洁云",
            "fund_nature": "operating_expense",
        },
    )
    assert response.status == 400


def test_finance_write_routes_cover_capital_withdrawal_funding_and_review(tmp_path):
    database = Database(tmp_path / "web.sqlite3")
    database.migrate()
    router = DashboardRouter(LedgerService(database), csrf_token="secret")
    cases = (
        (
            "/capital",
            {"partner": "洁云", "entry_type": "paid_in", "amount": "1000"},
        ),
        (
            "/withdrawals",
            {"partner": "苏姐", "amount": "100", "purpose": "备用金"},
        ),
        (
            "/ad-funding",
            {
                "platform": "shopee",
                "store": "shop",
                "entry_type": "recharge",
                "amount": "500",
                "source": "manual",
            },
        ),
        (
            "/review-costs",
            {
                "platform": "shopee",
                "store": "shop",
                "order_id": "REVIEW-1",
                "seller_sku": "SKU-1",
                "goods_cost": "20",
                "service_fee": "5",
            },
        ),
    )
    for path, fields in cases:
        response = router.post(
            path,
            {
                "csrf_token": "secret",
                "occurred_on": "2026-07-28",
                **fields,
            },
        )
        assert response.status == 303


def test_inventory_sku_cost_and_profit_routes_are_writable(tmp_path):
    database = Database(tmp_path / "web.sqlite3")
    database.migrate()
    router = DashboardRouter(
        LedgerService(database),
        csrf_token="secret",
        inventory=InventoryService(database),
        orders=OrderRepository(database),
        fulfillment=FulfillmentService(database),
        profit_sharing=ProfitSharingService(database),
    )
    common = {"csrf_token": "secret"}
    assert router.post(
        "/fulfillment",
        {
            **common,
            "platform": "shopee",
            "store": "shop",
            "seller_sku": "SKU-1",
            "effective_date": "2026-07-01",
            "mode": "supplier_fulfilled",
            "supply_status": "available",
            "note": "货盘",
        },
    ).status == 303
    assert router.post(
        "/sku-costs",
        {
            **common,
            "platform": "shopee",
            "store": "shop",
            "seller_sku": "SKU-1",
            "effective_date": "2026-07-01",
            "unit_cost_cny": "5",
            "note": "首版",
        },
    ).status == 303
    assert router.post(
        "/purchases",
        {
            **common,
            "receipt_id": "PO-1",
            "supplier": "工厂",
            "received_on": "2026-07-28",
            "seller_sku": "SKU-1",
            "quantity": "10",
            "unit_cost_cny": "5",
        },
    ).status == 303
    assert router.post(
        "/profit-agreements",
        {
            **common,
            "effective_from": "2026-01-01",
            "jieyun_share": "0.60",
            "sujie_share": "0.40",
        },
    ).status == 303
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO settlement_records VALUES(
              'settlement-1','shopee','shop','ORDER-1','2026-07-28',
              '100','CNY','1','100','manual','2026-07-28T00:00:00Z')
            """
        )
    assert router.post(
        "/profit-periods",
        {
            **common,
            "starts_on": "2026-07-01",
            "ends_on": "2026-07-31",
        },
    ).status == 303
    with database.connect() as connection:
        period_id = connection.execute(
            "SELECT id FROM profit_periods"
        ).fetchone()[0]
    assert router.post(
        "/profit-periods/confirm",
        {**common, "period_id": period_id},
    ).status == 303
    assert router.post(
        "/profit-payments",
        {
            **common,
            "period_id": period_id,
            "partner": "洁云",
            "amount_cny": "10",
            "paid_on": "2026-07-31",
            "status": "paid",
            "note": "首笔",
        },
    ).status == 303

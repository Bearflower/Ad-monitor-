from adwatch.dashboard.routes import DashboardRouter
from adwatch.ledger.service import LedgerService
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

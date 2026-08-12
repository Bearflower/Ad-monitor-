from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from adwatch.dashboard.routes import DashboardRouter
from adwatch.inventory.service import InventoryService
from adwatch.ledger.service import LedgerService
from adwatch.orders.fulfillment import FulfillmentService
from adwatch.orders.repository import OrderRepository
from adwatch.profit_sharing.service import ProfitSharingService
from adwatch.reconciliation.balances import BalanceReconciliationService
from adwatch.storage.db import Database
from adwatch.strategy.service import StrategyRuleService


def _seed_metrics(connection, data_date: date) -> None:
    """为测试插入一条广告指标数据，供重算测试使用。"""
    connection.execute(
        """
        INSERT INTO daily_ad_metrics(
            data_date, platform, store, account_id, campaign_id, sku_id,
            spend, attributed_gmv, orders, currency, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data_date.isoformat(),
            "shopee",
            "shop",
            "acc-1",
            "camp-1",
            "SKU-1",
            "100.00",
            "500.00",
            5,
            "THB",
            "ziniao",
        ),
    )
    connection.execute(
        """
        INSERT INTO product_costs(
            sku_id, effective_date, product_cost, commission_rate,
            seller_shipping, coupons, allocated_fixed_cost, refund_amount
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "SKU-1",
            data_date.isoformat(),
            "50.00",
            "0.10",
            "10.00",
            "0.00",
            "5.00",
            "0.00",
        ),
    )
    connection.execute(
        """
        INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
        VALUES (?, ?, ?)
        """,
        ("THB", data_date.isoformat(), "0.20"),
    )
    connection.execute(
        """
        INSERT INTO sku_mappings(sku_id) VALUES (?)
        """,
        ("SKU-1",),
    )


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


def test_finance_closure_routes_confirm_adjust_reverse_and_reconcile(tmp_path):
    database = Database(tmp_path / "closure-web.sqlite3")
    database.migrate()
    balance_service = BalanceReconciliationService(database)
    router = DashboardRouter(
        LedgerService(database),
        csrf_token="secret",
        inventory=InventoryService(database),
        profit_sharing=ProfitSharingService(database),
        balances=balance_service,
    )
    common = {"csrf_token": "secret"}
    assert router.post(
        "/expenses",
        {
            **common,
            "occurred_on": "2026-08-04",
            "category": "物流",
            "amount": "10",
            "currency": "CNY",
            "rate_to_cny": "1",
            "payer": "洁云",
            "fund_nature": "operating_expense",
        },
    ).location == "/ad-funds"
    with database.connect() as connection:
        expense_id = connection.execute(
            "SELECT id FROM expense_entries"
        ).fetchone()[0]
    assert router.post(
        "/expenses/confirm", {**common, "entry_id": expense_id}
    ).status == 303
    assert router.post(
        "/expenses/reverse",
        {**common, "entry_id": expense_id, "reason": "重复"},
    ).status == 303
    assert router.post(
        "/expenses/reverse",
        {**common, "entry_id": expense_id, "reason": "重复"},
    ).status == 400

    assert router.post(
        "/adjustments",
        {
            **common,
            "adjustment_type": "income",
            "amount_cny": "8",
            "occurred_on": "2026-08-04",
            "reason": "补记",
        },
    ).status == 303
    with database.connect() as connection:
        adjustment_id = connection.execute(
            "SELECT id FROM financial_adjustments WHERE reversal_of IS NULL"
        ).fetchone()[0]
    assert router.post(
        "/adjustments/reverse",
        {
            **common,
            "entry_id": adjustment_id,
            "occurred_on": "2026-08-04",
            "reason": "更正",
        },
    ).status == 303

    for account_type in ("cash", "platform", "bank"):
        assert router.post(
            "/balance-snapshots",
            {
                **common,
                "account_type": account_type,
                "account_name": account_type,
                "snapshot_date": "2026-08-04",
                "amount_cny": "0",
                "source": "manual",
            },
        ).status == 303
    assert router.post(
        "/balance-reconciliations",
        {**common, "data_date": "2026-08-04"},
    ).status == 303


def test_inventory_and_profit_reversal_routes_use_domain_guards(tmp_path):
    database = Database(tmp_path / "closure-domain-web.sqlite3")
    database.migrate()
    inventory = InventoryService(database)
    sharing = ProfitSharingService(database)
    router = DashboardRouter(
        LedgerService(database),
        csrf_token="secret",
        inventory=inventory,
        profit_sharing=sharing,
    )
    common = {"csrf_token": "secret"}
    assert router.post(
        "/inventory/adjust",
        {
            **common,
            "seller_sku": "SKU-1",
            "quantity_delta": "5",
            "occurred_on": "2026-08-04",
            "reason": "盘点",
        },
    ).status == 303
    with database.connect() as connection:
        movement_id = connection.execute(
            "SELECT id FROM inventory_movements"
        ).fetchone()[0]
    assert router.post(
        "/inventory/movements/reverse",
        {
            **common,
            "movement_id": movement_id,
            "occurred_on": "2026-08-04",
            "reason": "误录",
        },
    ).status == 303

    sharing.create_agreement(
        effective_from=date(2026, 1, 1),
        shares={"洁云": Decimal(1)},
        actor="test",
    )
    period = sharing.create_period(
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 7, 31),
        net_profit_cny=Decimal(100),
        actor="test",
    )
    sharing.confirm_period(period, actor="test")
    payment = sharing.record_payment(
        period_id=period,
        partner="洁云",
        amount_cny=Decimal(50),
        paid_on=date(2026, 8, 4),
        status="paid",
        note="",
        actor="test",
    )
    assert router.post(
        "/profit-payments/reverse",
        {
            **common,
            "payment_id": payment,
            "reversed_on": "2026-08-04",
            "reason": "账户错",
        },
    ).status == 303
    assert router.post(
        "/profit-periods/reverse",
        {**common, "period_id": period, "reason": "期间错"},
    ).status == 303


def test_sku_cost_purchase_and_profit_creation_routes_are_writable(tmp_path):
    database = Database(tmp_path / "creation-web.sqlite3")
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


def test_inventory_recalculate_route_runs_all_metric_days_and_reports_gaps(
    tmp_path,
):
    first_date = date(2026, 7, 27)
    data_date = date(2026, 7, 28)
    database = Database(tmp_path / "recalc-web.sqlite3")
    database.migrate()
    with database.connect() as connection:
        _seed_metrics(connection, first_date)
        connection.execute(
            """
            INSERT INTO daily_ad_metrics(
                data_date, platform, store, account_id, campaign_id, sku_id,
                spend, attributed_gmv, orders, currency, source
            ) VALUES (?, 'shopee', 'shop', 'acc-1', 'camp-1', 'SKU-1',
                      '80.00', '400.00', 4, 'THB', 'ziniao')
            """,
            (data_date.isoformat(),),
        )
    router = DashboardRouter(
        LedgerService(database),
        csrf_token="secret",
        database=database,
        inventory=InventoryService(database),
        orders=OrderRepository(database),
    )
    response = router.post(
        "/inventory/recalculate",
        {
            "csrf_token": "secret",
            "data_date": data_date.isoformat(),
        },
    )
    assert response.status == 303
    assert response.location is not None
    redirect = urlparse(response.location)
    query = parse_qs(redirect.query)
    assert redirect.path == "/inventory"
    assert query["recalc_status"] == ["warning"]
    assert "覆盖 2 个日期" in query["recalc_message"][0]
    assert "仍有 2 条待补数据" in query["recalc_message"][0]
    assert "退款金额 2" in query["recalc_message"][0]

    with database.connect() as connection:
        audit = connection.execute(
            """
            SELECT start_date, end_date, status, days_processed,
                   metrics_processed, profit_results, pending_data
            FROM analysis_recalculation_runs
            """
        ).fetchone()
    assert tuple(audit) == (
        first_date.isoformat(),
        data_date.isoformat(),
        "succeeded",
        2,
        2,
        0,
        2,
    )


def test_strategy_shadow_route_does_not_require_finance_date(tmp_path):
    database = Database(tmp_path / "shadow-route.sqlite3")
    database.migrate()
    router = DashboardRouter(
        LedgerService(database),
        csrf_token="secret",
        database=database,
        strategy_rule=StrategyRuleService(database),
    )

    response = router.post(
        "/strategy-rules/shadow",
        {"csrf_token": "secret", "parameters": "{}"},
    )

    assert response.status == 303
    assert response.location is not None
    assert response.location.startswith("/strategy-rules?shadow_results=")

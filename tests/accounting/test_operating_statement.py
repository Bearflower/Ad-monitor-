from datetime import date
from decimal import Decimal

from adwatch.accounting.operating_statement import OperatingStatementService
from adwatch.income.models import FeeStatement
from adwatch.income.repository import FeeStatementRepository
from adwatch.storage.db import Database


def test_monthly_statement_separates_cash_from_profit(tmp_path):
    database = Database(tmp_path / "statement.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO settlement_records VALUES "
            "('s','shopee','shop','O','2026-08-05','1000','CNY','1',"
            "'1000','test','2026-08-05')"
        )
        connection.execute(
            """
            INSERT INTO manual_income_entries VALUES(
              'i','','','','2026-08-05','50','CNY','1','50',
              '2026-08-05','identity','confirmed','','test','2026-08-05',NULL)
            """
        )
        connection.execute(
            "INSERT INTO exchange_rates(currency,rate_date,rate_to_cny) "
            "VALUES('THB','2026-08-05','0.2')"
        )
        connection.execute(
            """
            INSERT INTO platform_order_lines VALUES(
              'shopee','shop','O','item','model','SKU','', 'Product',1,
              '1000','CNY','completed','delivered','','2026-08-05','2026-08-05')
            """
        )
        connection.execute(
            """
            INSERT INTO order_fulfillment_snapshots VALUES(
              'shopee','shop','O','SKU','supplier_fulfilled','2026-08-01',
              'available','test','2026-08-05')
            """
        )
        connection.execute(
            """
            INSERT INTO order_cost_snapshots VALUES(
              'shopee','shop','O','SKU',1,'250','250','2026-08-01',
              'confirmed','2026-08-05')
            """
        )
        connection.execute(
            """
            INSERT INTO purchase_receipts(
              id,supplier,received_on,status,created_by,created_at
            ) VALUES('p','f','2026-08-01','confirmed','test','2026-08-01')
            """
        )
        connection.execute(
            "INSERT INTO purchase_lines(receipt_id,seller_sku,quantity,"
            "unit_cost_cny,line_cost_cny) VALUES('p','SKU',1,'300','300')"
        )
        connection.execute(
            "INSERT INTO ad_spend_entries VALUES "
            "('a','shopee','shop','C','100','CNY','1','100','2026-08-05',"
            "'test',NULL,'2026-08-05')"
        )
        connection.execute(
            """
            INSERT INTO expense_entries(
              id,occurred_on,category,amount_original,currency,rate_to_cny,
              amount_cny,payer,fund_nature,affects_profit,affects_capital,
              status,note,created_by,created_at
            ) VALUES('e','2026-08-05','office','40','CNY','1','40','p',
              'operating_expense',1,0,'confirmed','','test','2026-08-05')
            """
        )
        connection.execute(
            """
            INSERT INTO review_order_costs(
              id,platform,store,order_id,goods_cost_cny,service_fee_cny,
              occurred_on,status,created_at
            ) VALUES('r','shopee','shop','R','20','10','2026-08-05',
              'confirmed','2026-08-05')
            """
        )
    FeeStatementRepository(database).save(
        FeeStatement(
            platform="shopee", store="shop", source_store="shop",
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 5),
            currency="THB", product_price=Decimal(1000),
            commission_fee=Decimal(80), service_fee=Decimal(0),
            transaction_fee=Decimal(0), affiliate_fee=Decimal(0),
            sales_fee_total=Decimal(80), effective_rate=Decimal("0.080000"),
            seller_voucher=Decimal(0), shipping_paid_by_buyer=Decimal(0),
            shipping_provider_charge=Decimal(0), shipping_rebate=Decimal(0),
            ads_credit_topup=Decimal(0), adjustment_amount=Decimal(0),
            total_payout=Decimal(920), source_name="income.pdf",
            source_sha256="a" * 64,
        )
    )
    statement = OperatingStatementService(database).monthly(2026, 8)
    assert statement.revenue_cny == Decimal("1050.00")
    assert statement.purchase_cash_cny == Decimal("300.00")
    assert statement.cogs_cny == Decimal("250.00")
    assert statement.platform_fee_cny == Decimal("16.00")
    assert statement.ad_spend_cny == Decimal("100.00")
    assert statement.net_profit_cny == Decimal("614.00")


def test_yearly_returns_twelve_fixed_months(tmp_path):
    database = Database(tmp_path / "year.sqlite3")
    database.migrate()
    statements = OperatingStatementService(database).yearly(2026)
    assert len(statements) == 12
    assert statements[0].starts_on == date(2026, 1, 1)
    assert statements[-1].ends_on == date(2026, 12, 31)


def test_foreign_platform_fee_without_rate_blocks_profit(tmp_path):
    database = Database(tmp_path / "missing-rate.sqlite3")
    database.migrate()
    FeeStatementRepository(database).save(
        FeeStatement(
            platform="shopee", store="shop", source_store="shop",
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 5),
            currency="THB", product_price=Decimal(100),
            commission_fee=Decimal(10), service_fee=Decimal(0),
            transaction_fee=Decimal(0), affiliate_fee=Decimal(0),
            sales_fee_total=Decimal(10), effective_rate=Decimal("0.100000"),
            seller_voucher=Decimal(0), shipping_paid_by_buyer=Decimal(0),
            shipping_provider_charge=Decimal(0), shipping_rebate=Decimal(0),
            ads_credit_topup=Decimal(0), adjustment_amount=Decimal(0),
            total_payout=Decimal(90), source_name="missing-rate.pdf",
            source_sha256="b" * 64,
        )
    )
    statement = OperatingStatementService(database).monthly(2026, 8)
    assert statement.net_profit_cny is None
    assert "exchange_rate" in statement.missing_inputs

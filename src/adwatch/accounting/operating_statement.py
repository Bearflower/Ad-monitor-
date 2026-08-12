from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from adwatch.storage.db import Database


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class OperatingStatement:
    starts_on: date
    ends_on: date
    revenue_cny: Decimal
    purchase_cash_cny: Decimal
    cogs_cny: Decimal | None
    platform_fee_cny: Decimal
    ad_recharge_paid_cny: Decimal
    ad_recharge_credited_cny: Decimal
    ad_spend_cny: Decimal
    review_cost_cny: Decimal
    operating_expense_cny: Decimal
    net_profit_cny: Decimal | None
    capital_in_cny: Decimal
    withdrawal_cny: Decimal
    operating_cash_change_cny: Decimal
    completeness: str
    missing_inputs: tuple[str, ...]
    source_counts: dict[str, int]


class OperatingStatementService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def monthly(self, year: int, month: int) -> OperatingStatement:
        last_day = calendar.monthrange(year, month)[1]
        return self.between(date(year, month, 1), date(year, month, last_day))

    def yearly(self, year: int) -> tuple[OperatingStatement, ...]:
        return tuple(self.monthly(year, month) for month in range(1, 13))

    def between(self, starts_on: date, ends_on: date) -> OperatingStatement:
        if starts_on > ends_on:
            raise ValueError("statement start must not exceed end")
        start, end = starts_on.isoformat(), ends_on.isoformat()
        with self.database.connect() as connection:
            settlement, settlement_count = self._sum_count(
                connection,
                "settlement_records",
                "amount_cny",
                "settled_on",
                start,
                end,
            )
            manual_income, income_count = self._sum_count(
                connection,
                "manual_income_entries",
                "amount_cny",
                "occurred_on",
                start,
                end,
                "status='confirmed'",
            )
            income_adjustments, adjustment_count = self._sum_count(
                connection,
                "financial_adjustments",
                "amount_cny",
                "occurred_on",
                start,
                end,
                "adjustment_type='income' "
                "AND status IN ('confirmed', 'reversed')",
            )
            purchase, purchase_count = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(line.line_cost_cny AS REAL)), 0),
                       COUNT(DISTINCT receipt.id)
                FROM purchase_receipts AS receipt
                JOIN purchase_lines AS line ON line.receipt_id=receipt.id
                WHERE receipt.received_on BETWEEN ? AND ?
                  AND receipt.status='confirmed'
                """,
                (start, end),
            ).fetchone()
            cogs, cogs_count = self._cogs(connection, start, end)
            platform_fee, fee_count = connection.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN entry.currency='CNY'
                    THEN CAST(entry.amount_original AS REAL) ELSE 0 END), 0),
                       COUNT(*)
                FROM platform_fee_entries AS entry
                JOIN platform_fee_statements AS statement
                  ON statement.id=entry.statement_id
                WHERE statement.is_current=1
                  AND statement.period_end>=? AND statement.period_start<=?
                """,
                (start, end),
            ).fetchone()
            ad_spend, ad_spend_count = self._sum_count(
                connection, "ad_spend_entries", "amount_cny", "occurred_on",
                start, end
            )
            expense, expense_count = self._sum_count(
                connection, "expense_entries", "amount_cny", "occurred_on",
                start, end, "status='confirmed' AND affects_profit=1"
            )
            review, review_count = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(goods_cost_cny AS REAL)
                    + CAST(service_fee_cny AS REAL)), 0), COUNT(*)
                FROM review_order_costs
                WHERE occurred_on BETWEEN ? AND ? AND status='confirmed'
                """,
                (start, end),
            ).fetchone()
            funding = connection.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN entry_type='recharge'
                    THEN CAST(amount_cny AS REAL) ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN entry_type IN ('recharge','gift')
                    THEN CAST(credited_amount_cny AS REAL) ELSE 0 END), 0),
                       COUNT(*)
                FROM ad_funding_entries
                WHERE occurred_on BETWEEN ? AND ? AND status='confirmed'
                """,
                (start, end),
            ).fetchone()
            capital, capital_count = self._sum_count(
                connection, "capital_entries", "amount_cny", "occurred_on",
                start, end, "status='confirmed'"
            )
            withdrawal, withdrawal_count = self._sum_count(
                connection, "withdrawal_entries", "amount_cny", "occurred_on",
                start, end, "status='confirmed'"
            )
            profit_payments, payment_count = self._sum_count(
                connection, "profit_payments", "amount_cny", "paid_on",
                start, end, "status IN ('paid', 'reversed')"
            )
            missing = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT input_name FROM profit_input_lineage
                    WHERE data_date BETWEEN ? AND ?
                      AND status NOT IN ('ready', 'carried_forward')
                    ORDER BY input_name
                    """,
                    (start, end),
                )
            )

        revenue = (
            _money(settlement)
            + _money(manual_income)
            + _money(income_adjustments)
        )
        costs = (_money(cogs) + _money(platform_fee) + _money(ad_spend)
                 + _money(expense) + _money(review))
        pending_profit = bool(set(missing) & {"product_cost", "refunds"})
        net_profit = None if pending_profit else _money(revenue - costs)
        cash_change = _money(
            revenue - _money(purchase) - _money(funding[0])
            - _money(expense) - _money(review) + _money(capital)
            - _money(withdrawal) - _money(profit_payments)
        )
        counts = {
            "settlement_records": int(settlement_count),
            "manual_income_entries": int(income_count),
            "financial_adjustments": int(adjustment_count),
            "purchase_receipts": int(purchase_count),
            "order_cost_snapshots": int(cogs_count),
            "platform_fee_entries": int(fee_count),
            "ad_spend_entries": int(ad_spend_count),
            "expense_entries": int(expense_count),
            "review_order_costs": int(review_count),
            "ad_funding_entries": int(funding[2]),
            "capital_entries": int(capital_count),
            "withdrawal_entries": int(withdrawal_count),
            "profit_payments": int(payment_count),
        }
        return OperatingStatement(
            starts_on, ends_on, _money(revenue), _money(purchase),
            None if pending_profit else _money(cogs), _money(platform_fee),
            _money(funding[0]), _money(funding[1]), _money(ad_spend),
            _money(review), _money(expense), net_profit, _money(capital),
            _money(withdrawal), cash_change,
            "pending_data" if pending_profit else "complete", missing, counts,
        )

    @staticmethod
    def _sum_count(
        connection, table: str, amount: str, occurred: str,
        start: str, end: str, condition: str = "1=1"
    ):
        return connection.execute(
            f"SELECT COALESCE(SUM(CAST({amount} AS REAL)), 0), COUNT(*) "
            f"FROM {table} WHERE {occurred} BETWEEN ? AND ? AND {condition}",
            (start, end),
        ).fetchone()

    @staticmethod
    def _cogs(connection, start: str, end: str):
        return connection.execute(
            """
            WITH order_dates AS (
                SELECT platform, store, order_id, sku_identity AS seller_sku,
                       MIN(substr(ordered_at,1,10)) AS order_date
                FROM effective_order_sku_lines WHERE resolution_status='ready'
                GROUP BY platform, store, order_id, sku_identity
            ), cost_events AS (
                SELECT snapshot.total_cost_cny,
                       COALESCE(movement.occurred_on,
                         CASE WHEN fulfillment.mode='supplier_fulfilled'
                         THEN orders.order_date END) AS occurred_on
                FROM order_cost_snapshots AS snapshot
                LEFT JOIN order_fulfillment_snapshots AS fulfillment
                  ON fulfillment.platform=snapshot.platform
                 AND fulfillment.store=snapshot.store
                 AND fulfillment.order_id=snapshot.order_id
                 AND fulfillment.seller_sku=snapshot.seller_sku
                LEFT JOIN inventory_movements AS movement
                  ON movement.source_type='order'
                 AND movement.movement_type='sale_out'
                 AND movement.source_id=snapshot.platform || ':' ||
                     snapshot.store || ':' || snapshot.order_id
                 AND movement.seller_sku=snapshot.seller_sku
                LEFT JOIN order_dates AS orders
                  ON orders.platform=snapshot.platform
                 AND orders.store=snapshot.store
                 AND orders.order_id=snapshot.order_id
                 AND orders.seller_sku=snapshot.seller_sku
                WHERE snapshot.status='confirmed'
            )
            SELECT COALESCE(SUM(CAST(total_cost_cny AS REAL)), 0), COUNT(*)
            FROM cost_events WHERE occurred_on BETWEEN ? AND ?
            """,
            (start, end),
        ).fetchone()

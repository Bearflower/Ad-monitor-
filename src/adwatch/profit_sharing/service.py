from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from adwatch.storage.db import Database


class ProfitSharingError(ValueError):
    pass


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ProfitSharingService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_agreement(
        self,
        *,
        effective_from: date,
        shares: dict[str, Decimal],
        actor: str,
    ) -> str:
        if not shares or sum(shares.values(), Decimal(0)) != Decimal(1):
            raise ProfitSharingError("share ratios must total one")
        if any(value <= 0 for value in shares.values()):
            raise ProfitSharingError("share ratios must be positive")
        agreement_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            latest = connection.execute(
                """
                SELECT id, effective_from, version
                FROM profit_share_agreements
                ORDER BY effective_from DESC LIMIT 1
                """
            ).fetchone()
            if latest and effective_from.isoformat() <= latest["effective_from"]:
                raise ProfitSharingError(
                    "new agreement must start after the latest agreement"
                )
            version = 1 if latest is None else int(latest["version"]) + 1
            if latest:
                connection.execute(
                    "UPDATE profit_share_agreements SET effective_to=? WHERE id=?",
                    (
                        date.fromordinal(effective_from.toordinal() - 1).isoformat(),
                        latest["id"],
                    ),
                )
            connection.execute(
                """
                INSERT INTO profit_share_agreements(
                    id, effective_from, version, shares_json,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    agreement_id,
                    effective_from.isoformat(),
                    version,
                    json.dumps(
                        {key: str(value) for key, value in shares.items()},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    actor,
                    now,
                ),
            )
        return agreement_id

    def create_period(
        self,
        *,
        starts_on: date,
        ends_on: date,
        net_profit_cny: Decimal,
        actor: str,
    ) -> str:
        if starts_on > ends_on:
            raise ProfitSharingError("period start must not exceed end")
        with self.database.transaction() as connection:
            agreement = connection.execute(
                """
                SELECT * FROM profit_share_agreements
                WHERE effective_from <= ?
                  AND (effective_to IS NULL OR effective_to >= ?)
                ORDER BY effective_from DESC LIMIT 1
                """,
                (starts_on.isoformat(), starts_on.isoformat()),
            ).fetchone()
            if agreement is None:
                raise ProfitSharingError("no effective profit agreement")
            period_id = str(uuid.uuid4())
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO profit_periods(
                    id, starts_on, ends_on, agreement_id, net_profit_cny,
                    status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    period_id,
                    starts_on.isoformat(),
                    ends_on.isoformat(),
                    agreement["id"],
                    str(_money(net_profit_cny)),
                    actor,
                    now,
                ),
            )
            shares = json.loads(agreement["shares_json"])
            for partner, ratio_text in shares.items():
                ratio = Decimal(ratio_text)
                connection.execute(
                    """
                    INSERT INTO profit_allocations(
                        period_id, partner, share_ratio, amount_cny
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        period_id,
                        partner,
                        str(ratio),
                        str(_money(net_profit_cny * ratio)),
                    ),
                )
        return period_id

    def create_period_from_ledger(
        self, *, starts_on: date, ends_on: date, actor: str
    ) -> str:
        start, end = starts_on.isoformat(), ends_on.isoformat()
        with self.database.connect() as connection:
            revenue = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM settlement_records WHERE settled_on BETWEEN ? AND ?
                """,
                (start, end),
            ).fetchone()[0]
            cogs = connection.execute(
                """
                WITH order_dates AS (
                    SELECT platform, store, order_id, seller_sku,
                           MIN(substr(ordered_at, 1, 10)) AS order_date
                    FROM platform_order_lines
                    GROUP BY platform, store, order_id, seller_sku
                ),
                cost_events AS (
                    SELECT
                        snapshot.total_cost_cny,
                        COALESCE(
                            movement.occurred_on,
                            CASE WHEN fulfillment.mode='supplier_fulfilled'
                                 THEN order_dates.order_date END
                        ) AS occurred_on
                    FROM order_cost_snapshots AS snapshot
                    LEFT JOIN order_fulfillment_snapshots AS fulfillment
                      ON fulfillment.platform=snapshot.platform
                     AND fulfillment.store=snapshot.store
                     AND fulfillment.order_id=snapshot.order_id
                     AND fulfillment.seller_sku=snapshot.seller_sku
                    LEFT JOIN inventory_movements AS movement
                  ON movement.source_type='order'
                 AND movement.movement_type='sale_out'
                 AND movement.source_id=(
                    snapshot.platform || ':' || snapshot.store || ':'
                    || snapshot.order_id
                 )
                 AND movement.seller_sku=snapshot.seller_sku
                    LEFT JOIN order_dates
                      ON order_dates.platform=snapshot.platform
                     AND order_dates.store=snapshot.store
                     AND order_dates.order_id=snapshot.order_id
                     AND order_dates.seller_sku=snapshot.seller_sku
                    WHERE snapshot.status='confirmed'
                )
                SELECT COALESCE(SUM(CAST(total_cost_cny AS REAL)), 0)
                FROM cost_events
                WHERE occurred_on BETWEEN ? AND ?
                """,
                (start, end),
            ).fetchone()[0]
            ad_spend = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM ad_spend_entries WHERE occurred_on BETWEEN ? AND ?
                """,
                (start, end),
            ).fetchone()[0]
            expenses = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM expense_entries
                WHERE occurred_on BETWEEN ? AND ? AND status='confirmed'
                  AND affects_profit=1
                """,
                (start, end),
            ).fetchone()[0]
            review_costs = connection.execute(
                """
                SELECT COALESCE(SUM(
                    CAST(goods_cost_cny AS REAL)
                    + CAST(service_fee_cny AS REAL)
                ), 0)
                FROM review_order_costs
                WHERE occurred_on BETWEEN ? AND ? AND status='confirmed'
                """,
                (start, end),
            ).fetchone()[0]
        net_profit = Decimal(str(revenue)) - sum(
            (
                Decimal(str(cogs)),
                Decimal(str(ad_spend)),
                Decimal(str(expenses)),
                Decimal(str(review_costs)),
            ),
            Decimal(0),
        )
        return self.create_period(
            starts_on=starts_on,
            ends_on=ends_on,
            net_profit_cny=net_profit,
            actor=actor,
        )

    def confirm_period(self, period_id: str, *, actor: str) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM profit_periods WHERE id=?", (period_id,)
            ).fetchone()
            if row is None or row["status"] != "draft":
                raise ProfitSharingError("only draft periods can be confirmed")
            connection.execute(
                "UPDATE profit_periods SET status='confirmed' WHERE id=?",
                (period_id,),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, object_type, object_id, action,
                    after_json, actor, created_at
                ) VALUES (?, 'profit_period', ?, 'confirm', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    period_id,
                    '{"status":"confirmed"}',
                    actor,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def record_payment(
        self,
        *,
        period_id: str,
        partner: str,
        amount_cny: Decimal,
        paid_on: date,
        status: str,
        note: str,
        actor: str,
    ) -> str:
        amount = _money(amount_cny)
        if amount <= 0 or status not in {"planned", "paid", "reversed"}:
            raise ProfitSharingError("invalid payment amount or status")
        payment_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            allocation = connection.execute(
                """
                SELECT a.amount_cny, p.status
                FROM profit_allocations a
                JOIN profit_periods p ON p.id=a.period_id
                WHERE a.period_id=? AND a.partner=?
                """,
                (period_id, partner),
            ).fetchone()
            if allocation is None or allocation["status"] != "confirmed":
                raise ProfitSharingError("period must be confirmed")
            paid = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM profit_payments
                WHERE period_id=? AND partner=? AND status='paid'
                """,
                (period_id, partner),
            ).fetchone()[0]
            if status == "paid" and Decimal(str(paid)) + amount > Decimal(
                allocation["amount_cny"]
            ):
                raise ProfitSharingError("payment exceeds allocation")
            connection.execute(
                """
                INSERT INTO profit_payments(
                    id, period_id, partner, amount_cny,
                    paid_on, status, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_id,
                    period_id,
                    partner,
                    str(amount),
                    paid_on.isoformat(),
                    status,
                    note,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, object_type, object_id, action,
                    after_json, actor, created_at
                ) VALUES (?, 'profit_payment', ?, 'create', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    payment_id,
                    json.dumps(
                        {"status": status, "amount_cny": str(amount)},
                        sort_keys=True,
                    ),
                    actor,
                    now,
                ),
            )
        return payment_id

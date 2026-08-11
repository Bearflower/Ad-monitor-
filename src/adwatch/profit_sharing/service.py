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
        actor = self._required(actor, "actor")
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
                effective_to = date.fromordinal(
                    effective_from.toordinal() - 1
                ).isoformat()
                connection.execute(
                    "UPDATE profit_share_agreements SET effective_to=? WHERE id=?",
                    (effective_to, latest["id"]),
                )
                self._audit(
                    connection,
                    "profit_agreement",
                    str(latest["id"]),
                    "supersede",
                    actor,
                    "new agreement version",
                    {"effective_to": None},
                    {"effective_to": effective_to},
                    now,
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
            self._audit(
                connection,
                "profit_agreement",
                agreement_id,
                "create",
                actor,
                None,
                None,
                {
                    "effective_from": effective_from.isoformat(),
                    "version": version,
                },
                now,
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
        actor = self._required(actor, "actor")
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
            self._audit(
                connection,
                "profit_period",
                period_id,
                "create",
                actor,
                None,
                None,
                {
                    "status": "draft",
                    "starts_on": starts_on.isoformat(),
                    "ends_on": ends_on.isoformat(),
                    "net_profit_cny": str(_money(net_profit_cny)),
                },
                now,
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
            income_adjustments = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM financial_adjustments
                WHERE occurred_on BETWEEN ? AND ?
                  AND adjustment_type='income'
                  AND status IN ('confirmed', 'reversed')
                """,
                (start, end),
            ).fetchone()[0]
            cogs = connection.execute(
                """
                WITH order_dates AS (
                    SELECT platform, store, order_id, seller_sku,
                           MIN(substr(ordered_at, 1, 10)) AS order_date
                    FROM effective_platform_order_lines
                    WHERE resolution_status='ready'
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
        net_profit = (
            Decimal(str(revenue)) + Decimal(str(income_adjustments))
        ) - sum(
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
        actor = self._required(actor, "actor")
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
                    id, object_type, object_id, action, before_json,
                    after_json, actor, created_at
                ) VALUES (
                    ?, 'profit_period', ?, 'confirm', ?, ?, ?, ?
                )
                """,
                (
                    str(uuid.uuid4()),
                    period_id,
                    '{"status":"draft"}',
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
        actor = self._required(actor, "actor")
        if amount <= 0 or status not in {"planned", "paid"}:
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
                WHERE period_id=? AND partner=?
                  AND status IN ('paid', 'reversed')
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
                    paid_on, status, note, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    actor,
                ),
            )
            if status == "paid":
                connection.execute(
                    """
                    INSERT INTO cash_movements(
                        id, occurred_on, movement_type, amount_cny,
                        source_type, source_id, created_at
                    ) VALUES (
                        ?, ?, 'profit_payment', ?, 'profit_payment', ?, ?
                    )
                    """,
                    (
                        str(uuid.uuid4()),
                        paid_on.isoformat(),
                        str(-amount),
                        payment_id,
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

    def reverse_payment(
        self,
        payment_id: str,
        *,
        reversed_on: date,
        reason: str,
        actor: str,
    ) -> str:
        reason = self._required(reason, "reversal reason")
        actor = self._required(actor, "actor")
        reversal_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            original = connection.execute(
                "SELECT * FROM profit_payments WHERE id=?",
                (payment_id,),
            ).fetchone()
            if (
                original is None
                or original["status"] != "paid"
                or original["reversal_of"] is not None
            ):
                raise ProfitSharingError("only paid payments can be reversed")
            exists = connection.execute(
                "SELECT 1 FROM profit_payments WHERE reversal_of=?",
                (payment_id,),
            ).fetchone()
            if exists is not None:
                raise ProfitSharingError("payment already reversed")
            original_cash = connection.execute(
                """
                SELECT id FROM cash_movements
                WHERE source_type='profit_payment' AND source_id=?
                  AND reversal_of IS NULL
                """,
                (payment_id,),
            ).fetchone()
            if original_cash is None:
                raise ProfitSharingError("payment cash fact is missing")
            amount = -_money(Decimal(original["amount_cny"]))
            connection.execute(
                """
                INSERT INTO profit_payments(
                    id, period_id, partner, amount_cny, paid_on, status,
                    note, created_at, created_by, reversal_reason, reversal_of
                ) VALUES (?, ?, ?, ?, ?, 'reversed', ?, ?, ?, ?, ?)
                """,
                (
                    reversal_id,
                    original["period_id"],
                    original["partner"],
                    str(amount),
                    reversed_on.isoformat(),
                    f"冲销 {payment_id}",
                    now,
                    actor,
                    reason,
                    payment_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO cash_movements(
                    id, occurred_on, movement_type, amount_cny,
                    source_type, source_id, reversal_of, created_at
                ) VALUES (
                    ?, ?, 'profit_payment_reversal', ?,
                    'profit_payment', ?, ?, ?
                )
                """,
                (
                    str(uuid.uuid4()),
                    reversed_on.isoformat(),
                    str(-amount),
                    reversal_id,
                    original_cash["id"],
                    now,
                ),
            )
            self._audit(
                connection,
                "profit_payment",
                payment_id,
                "reverse",
                actor,
                reason,
                {"status": "paid"},
                {"reversal_id": reversal_id},
                now,
            )
            self._audit(
                connection,
                "profit_payment",
                reversal_id,
                "create",
                actor,
                reason,
                None,
                {"status": "reversed", "reversal_of": payment_id},
                now,
            )
        return reversal_id

    def reverse_period(
        self, period_id: str, *, reason: str, actor: str
    ) -> None:
        reason = self._required(reason, "reversal reason")
        actor = self._required(actor, "actor")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            period = connection.execute(
                "SELECT * FROM profit_periods WHERE id=?", (period_id,)
            ).fetchone()
            if period is None or period["status"] != "confirmed":
                raise ProfitSharingError(
                    "only confirmed profit periods can be reversed"
                )
            outstanding = connection.execute(
                """
                SELECT 1 FROM profit_payments AS payment
                WHERE payment.period_id=? AND payment.status='paid'
                  AND payment.reversal_of IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM profit_payments AS reversal
                      WHERE reversal.reversal_of=payment.id
                  )
                LIMIT 1
                """,
                (period_id,),
            ).fetchone()
            if outstanding is not None:
                raise ProfitSharingError(
                    "period has unreversed payments"
                )
            connection.execute(
                """
                UPDATE profit_periods
                SET status='reversed', reversal_reason=? WHERE id=?
                """,
                (reason, period_id),
            )
            self._audit(
                connection,
                "profit_period",
                period_id,
                "reverse",
                actor,
                reason,
                {"status": "confirmed"},
                {"status": "reversed"},
                now,
            )

    def list_agreements(self) -> tuple[dict, ...]:
        """查询分润协议版本历史

        返回所有协议版本，按生效日期降序排列
        返回字段: id, effective_from, effective_to, version, shares_json
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, effective_from, effective_to, version, shares_json
                FROM profit_share_agreements
                ORDER BY effective_from DESC
                """
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def get_current_agreement(self, *, as_of: date | None = None) -> dict | None:
        """查询指定日期生效的分润协议

        默认返回当前最新协议
        参数: as_of - 查询日期，为 None 时返回最新协议
        返回字段: id, effective_from, effective_to, version, shares_json
        """
        with self.database.connect() as connection:
            if as_of is not None:
                row = connection.execute(
                    """
                    SELECT id, effective_from, effective_to, version, shares_json
                    FROM profit_share_agreements
                    WHERE effective_from <= ?
                      AND (effective_to IS NULL OR effective_to >= ?)
                    ORDER BY effective_from DESC LIMIT 1
                    """,
                    (as_of.isoformat(), as_of.isoformat()),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT id, effective_from, effective_to, version, shares_json
                    FROM profit_share_agreements
                    ORDER BY effective_from DESC LIMIT 1
                    """
                ).fetchone()
            return dict(row) if row else None

    def list_profit_periods(
        self,
        *,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[dict, ...]:
        """查询分润期间列表

        支持按状态（draft/confirmed）、日期范围筛选
        参数:
            status - 期间状态，可选值: draft, confirmed
            start_date - 起始日期，筛选 starts_on >= start_date
            end_date - 结束日期，筛选 ends_on <= end_date
        返回字段: id, starts_on, ends_on, agreement_id, net_profit_cny, status, created_at
        """
        query = """
            SELECT id, starts_on, ends_on, agreement_id,
                   net_profit_cny, status, reversal_reason,
                   created_by, created_at
            FROM profit_periods
            WHERE 1=1
        """
        params: list[object] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if start_date is not None:
            query += " AND starts_on >= ?"
            params.append(start_date.isoformat())
        if end_date is not None:
            query += " AND ends_on <= ?"
            params.append(end_date.isoformat())
        query += " ORDER BY starts_on DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT object_id, id FROM evidence_documents
                WHERE object_type='profit_period'
                ORDER BY created_at, id
                """
            ).fetchall()
        evidence: dict[str, list[str]] = {}
        for row in evidence_rows:
            evidence.setdefault(str(row["object_id"]), []).append(
                str(row["id"])
            )
        return tuple(
            {
                **dict(row),
                "evidence_ids": tuple(evidence.get(str(row["id"]), [])),
            }
            for row in rows
        )

    def list_profit_payments(
        self,
        *,
        period_id: str | None = None,
        partner: str | None = None,
        status: str | None = None,
    ) -> tuple[dict, ...]:
        """查询分润支付记录

        支持按期间、合伙人、状态筛选
        参数:
            period_id - 期间ID
            partner - 合伙人名称
            status - 支付状态，可选值: planned, paid, reversed
        返回字段: id, period_id, partner, amount_cny, paid_on, status
        """
        query = """
            SELECT id, period_id, partner, amount_cny, paid_on, status,
                   note, created_by, reversal_reason, reversal_of, created_at
            FROM profit_payments
            WHERE 1=1
        """
        params: list[object] = []
        if period_id is not None:
            query += " AND period_id = ?"
            params.append(period_id)
        if partner is not None:
            query += " AND partner = ?"
            params.append(partner)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY paid_on DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT object_id, id FROM evidence_documents
                WHERE object_type='profit_payment'
                ORDER BY created_at, id
                """
            ).fetchall()
        evidence: dict[str, list[str]] = {}
        for row in evidence_rows:
            evidence.setdefault(str(row["object_id"]), []).append(
                str(row["id"])
            )
        return tuple(
            {
                **dict(row),
                "evidence_ids": tuple(evidence.get(str(row["id"]), [])),
            }
            for row in rows
        )

    def calculate_net_profit_breakdown(
        self,
        *,
        starts_on: date,
        ends_on: date,
    ) -> dict:
        """计算指定期间的净利润明细

        分解为：收入、销售成本、平台费、广告费、经营费用、刷单成本
        参数:
            starts_on - 起始日期
            ends_on - 结束日期
        返回字段: revenue, cogs, platform_fee, ad_spend,
                  operating_expenses, review_costs, net_profit
        """
        start, end = starts_on.isoformat(), ends_on.isoformat()
        with self.database.connect() as connection:
            # 收入: 结算记录总额
            revenue = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM settlement_records
                WHERE settled_on BETWEEN ? AND ?
                """,
                (start, end),
            ).fetchone()[0]
            income_adjustments = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM financial_adjustments
                WHERE occurred_on BETWEEN ? AND ?
                  AND adjustment_type='income'
                  AND status IN ('confirmed', 'reversed')
                """,
                (start, end),
            ).fetchone()[0]

            # 销售成本: 订单成本快照（参考 create_period_from_ledger 逻辑）
            cogs = connection.execute(
                """
                WITH order_dates AS (
                    SELECT platform, store, order_id, seller_sku,
                           MIN(substr(ordered_at, 1, 10)) AS order_date
                    FROM effective_platform_order_lines
                    WHERE resolution_status='ready'
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

            # 广告费: 广告支出总额
            ad_spend = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM ad_spend_entries
                WHERE occurred_on BETWEEN ? AND ?
                """,
                (start, end),
            ).fetchone()[0]

            # 经营费用: 确认的、影响利润的支出
            operating_expenses = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(amount_cny AS REAL)), 0)
                FROM expense_entries
                WHERE occurred_on BETWEEN ? AND ?
                  AND status='confirmed'
                  AND affects_profit=1
                """,
                (start, end),
            ).fetchone()[0]

            # 刷单成本: 刷单订单费用
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

            # 平台费: 从结算记录中分离（若需要独立统计，此处可调整）
            # 目前平台费已包含在收入中，如需拆分可另行实现

        # 汇总计算净利润
        total_revenue = Decimal(str(revenue)) + Decimal(
            str(income_adjustments)
        )
        net_profit = total_revenue - sum(
            (
                Decimal(str(cogs)),
                Decimal(str(ad_spend)),
                Decimal(str(operating_expenses)),
                Decimal(str(review_costs)),
            ),
            Decimal(0),
        )

        return {
            "revenue": _money(total_revenue),
            "income_adjustments": _money(Decimal(str(income_adjustments))),
            "cogs": _money(Decimal(str(cogs))),
            "platform_fee": _money(Decimal(0)),
            "ad_spend": _money(Decimal(str(ad_spend))),
            "operating_expenses": _money(Decimal(str(operating_expenses))),
            "review_costs": _money(Decimal(str(review_costs))),
            "net_profit": _money(net_profit),
        }

    @staticmethod
    def _required(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ProfitSharingError(f"{label} is required")
        return normalized

    @staticmethod
    def _audit(
        connection,
        object_type: str,
        object_id: str,
        action: str,
        actor: str,
        reason: str | None,
        before: dict | None,
        after: dict | None,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                id, object_type, object_id, action, before_json,
                after_json, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                object_type,
                object_id,
                action,
                None if before is None else json.dumps(before, sort_keys=True),
                None if after is None else json.dumps(after, sort_keys=True),
                actor,
                reason,
                now,
            ),
        )

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from adwatch.ledger.models import (
    ExpenseDraft,
    ExpenseEntry,
    FinancialAdjustment,
)
from adwatch.storage.db import Database


class LedgerError(ValueError):
    pass


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class LedgerService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_expense(self, draft: ExpenseDraft, *, actor: str) -> ExpenseEntry:
        if draft.amount_original <= 0 or draft.rate_to_cny <= 0:
            raise LedgerError("amount and rate must be positive")
        if not draft.category.strip() or not actor.strip():
            raise LedgerError("category and actor are required")
        actor = actor.strip()
        entry_id = str(uuid.uuid4())
        amount_cny = _money(draft.amount_original * draft.rate_to_cny)
        now = datetime.now(UTC).isoformat()
        after = {
            "status": "draft",
            "amount_cny": str(amount_cny),
            "fund_nature": draft.fund_nature,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO expense_entries(
                    id, occurred_on, category, amount_original, currency,
                    rate_to_cny, amount_cny, payer, fund_nature,
                    affects_profit, affects_capital, status, note,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
                """,
                (
                    entry_id,
                    draft.occurred_on.isoformat(),
                    draft.category.strip(),
                    str(draft.amount_original),
                    draft.currency.upper(),
                    str(draft.rate_to_cny),
                    str(amount_cny),
                    draft.payer.strip(),
                    draft.fund_nature,
                    int(draft.affects_profit),
                    int(draft.affects_capital),
                    draft.note,
                    actor,
                    now,
                ),
            )
            self._audit(
                connection, entry_id, "create", actor, None, None, after, now
            )
        return ExpenseEntry(entry_id, "draft", amount_cny)

    def confirm_expense(self, entry_id: str, *, actor: str) -> None:
        actor = self._required(actor, "actor")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM expense_entries WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None or row["status"] != "draft":
                raise LedgerError("only draft expenses can be confirmed")
            connection.execute(
                "UPDATE expense_entries SET status='confirmed' WHERE id=?",
                (entry_id,),
            )
            connection.execute(
                """
                INSERT INTO cash_movements(
                    id, occurred_on, movement_type, amount_cny,
                    source_type, source_id, created_at
                ) VALUES (?, ?, 'expense_payment', ?, 'expense', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    row["occurred_on"],
                    str(-Decimal(row["amount_cny"])),
                    entry_id,
                    now,
                ),
            )
            self._audit(
                connection,
                entry_id,
                "confirm",
                actor,
                None,
                {"status": "draft"},
                {"status": "confirmed"},
                now,
            )

    def reverse_expense(
        self, entry_id: str, *, actor: str, reason: str
    ) -> None:
        actor = self._required(actor, "actor")
        reason = self._required(reason, "reversal reason")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM expense_entries WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None or row["status"] != "confirmed":
                raise LedgerError("only confirmed expenses can be reversed")
            original = connection.execute(
                """
                SELECT id FROM cash_movements
                WHERE source_type='expense' AND source_id=?
                  AND movement_type='expense_payment'
                """,
                (entry_id,),
            ).fetchone()
            connection.execute(
                """
                UPDATE expense_entries
                SET status='reversed', reversal_reason=? WHERE id=?
                """,
                (reason, entry_id),
            )
            connection.execute(
                """
                INSERT INTO cash_movements(
                    id, occurred_on, movement_type, amount_cny,
                    source_type, source_id, reversal_of, created_at
                ) VALUES (?, ?, 'expense_reversal', ?, 'expense', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    row["occurred_on"],
                    row["amount_cny"],
                    entry_id,
                    original["id"],
                    now,
                ),
            )
            self._audit(
                connection,
                entry_id,
                "reverse",
                actor,
                reason,
                {"status": "confirmed"},
                {"status": "reversed"},
                now,
            )

    @staticmethod
    def _positive(amount: Decimal) -> Decimal:
        if amount <= 0:
            raise LedgerError("amount must be positive")
        return _money(amount)

    def create_capital(
        self,
        *,
        partner: str,
        entry_type: str,
        amount: Decimal,
        occurred_on,
        actor: str,
    ) -> str:
        partner = self._required(partner, "partner")
        entry_type = self._required(entry_type, "entry type")
        actor = self._required(actor, "actor")
        value = self._positive(amount)
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO capital_entries(
                    id, partner, entry_type, amount_original, currency,
                    rate_to_cny, amount_cny, occurred_on, status,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, 'CNY', '1', ?, ?, 'confirmed', ?, ?)
                """,
                (
                    entry_id,
                    partner,
                    entry_type,
                    str(value),
                    str(value),
                    occurred_on.isoformat(),
                    now,
                    actor,
                ),
            )
            self._cash(
                connection, occurred_on, "capital_in", value, "capital", entry_id, now
            )
            self._audit_generic(
                connection, "capital", entry_id, actor, {"status": "confirmed"}, now
            )
        return entry_id

    def create_withdrawal(
        self,
        *,
        partner: str,
        amount: Decimal,
        occurred_on,
        purpose: str,
        actor: str,
    ) -> str:
        partner = self._required(partner, "partner")
        purpose = self._required(purpose, "purpose")
        actor = self._required(actor, "actor")
        value = self._positive(amount)
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO withdrawal_entries(
                    id, partner, amount_original, currency, rate_to_cny,
                    amount_cny, occurred_on, purpose, status, created_at,
                    created_by
                ) VALUES (?, ?, ?, 'CNY', '1', ?, ?, ?, 'confirmed', ?, ?)
                """,
                (
                    entry_id,
                    partner,
                    str(value),
                    str(value),
                    occurred_on.isoformat(),
                    purpose,
                    now,
                    actor,
                ),
            )
            self._cash(
                connection,
                occurred_on,
                "partner_withdrawal",
                -value,
                "withdrawal",
                entry_id,
                now,
            )
            self._audit_generic(
                connection,
                "withdrawal",
                entry_id,
                actor,
                {"status": "confirmed"},
                now,
            )
        return entry_id

    def create_ad_funding(
        self,
        *,
        platform: str,
        store: str,
        entry_type: str,
        amount: Decimal,
        occurred_on,
        source: str,
        actor: str,
    ) -> str:
        platform = self._required(platform, "platform")
        store = self._required(store, "store")
        entry_type = self._required(entry_type, "entry type")
        source = self._required(source, "source")
        actor = self._required(actor, "actor")
        value = self._positive(amount)
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ad_funding_entries(
                    id, platform, store, entry_type, amount_original,
                    currency, rate_to_cny, amount_cny, occurred_on,
                    source, status, created_at, created_by
                ) VALUES (
                    ?, ?, ?, ?, ?, 'CNY', '1', ?, ?, ?, 'confirmed', ?, ?
                )
                """,
                (
                    entry_id,
                    platform,
                    store,
                    entry_type,
                    str(value),
                    str(value),
                    occurred_on.isoformat(),
                    source,
                    now,
                    actor,
                ),
            )
            delta = value if entry_type in {"refund", "gift"} else -value
            self._cash(
                connection,
                occurred_on,
                "ad_funding",
                delta,
                "ad_funding",
                entry_id,
                now,
            )
            self._audit_generic(
                connection,
                "ad_funding",
                entry_id,
                actor,
                {"status": "confirmed"},
                now,
            )
        return entry_id

    def create_review_order_cost(
        self,
        *,
        platform: str,
        store: str,
        order_id: str,
        seller_sku: str,
        goods_cost: Decimal,
        service_fee: Decimal,
        occurred_on,
        actor: str,
    ) -> str:
        platform = self._required(platform, "platform")
        store = self._required(store, "store")
        order_id = self._required(order_id, "order id")
        actor = self._required(actor, "actor")
        if goods_cost < 0 or service_fee < 0 or goods_cost + service_fee <= 0:
            raise LedgerError("review order cost must be positive")
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        total = _money(goods_cost + service_fee)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO review_order_costs(
                    id, platform, store, order_id, seller_sku,
                    goods_cost_cny, service_fee_cny, occurred_on,
                    status, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)
                """,
                (
                    entry_id,
                    platform,
                    store,
                    order_id,
                    seller_sku,
                    str(_money(goods_cost)),
                    str(_money(service_fee)),
                    occurred_on.isoformat(),
                    now,
                    actor,
                ),
            )
            self._cash(
                connection,
                occurred_on,
                "review_order_payment",
                -total,
                "review_order",
                entry_id,
                now,
            )
            self._audit_generic(
                connection,
                "review_order",
                entry_id,
                actor,
                {"status": "confirmed", "excluded": True},
                now,
            )
        return entry_id

    def reverse_capital(
        self, entry_id: str, *, actor: str, reason: str
    ) -> None:
        self._reverse_entry(
            table="capital_entries",
            object_type="capital",
            source_type="capital",
            entry_id=entry_id,
            actor=actor,
            reason=reason,
        )

    def reverse_withdrawal(
        self, entry_id: str, *, actor: str, reason: str
    ) -> None:
        self._reverse_entry(
            table="withdrawal_entries",
            object_type="withdrawal",
            source_type="withdrawal",
            entry_id=entry_id,
            actor=actor,
            reason=reason,
        )

    def reverse_ad_funding(
        self, entry_id: str, *, actor: str, reason: str
    ) -> None:
        self._reverse_entry(
            table="ad_funding_entries",
            object_type="ad_funding",
            source_type="ad_funding",
            entry_id=entry_id,
            actor=actor,
            reason=reason,
        )

    def reverse_review_order_cost(
        self, entry_id: str, *, actor: str, reason: str
    ) -> None:
        self._reverse_entry(
            table="review_order_costs",
            object_type="review_order",
            source_type="review_order",
            entry_id=entry_id,
            actor=actor,
            reason=reason,
        )

    def create_adjustment(
        self,
        *,
        adjustment_type: str,
        amount_cny: Decimal,
        occurred_on: date,
        reason: str,
        actor: str,
    ) -> str:
        if adjustment_type not in {"income", "cash", "balance"}:
            raise LedgerError("invalid adjustment type")
        amount = _money(amount_cny)
        if amount == 0:
            raise LedgerError("adjustment amount must be non-zero")
        reason = self._required(reason, "reason")
        actor = self._required(actor, "actor")
        adjustment_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO financial_adjustments(
                    id, adjustment_type, amount_cny, occurred_on, reason,
                    status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?)
                """,
                (
                    adjustment_id,
                    adjustment_type,
                    str(amount),
                    occurred_on.isoformat(),
                    reason,
                    actor,
                    now,
                ),
            )
            self._cash(
                connection,
                occurred_on,
                "financial_adjustment",
                amount,
                "financial_adjustment",
                adjustment_id,
                now,
            )
            self._audit_generic(
                connection,
                "financial_adjustment",
                adjustment_id,
                actor,
                {
                    "status": "confirmed",
                    "adjustment_type": adjustment_type,
                    "amount_cny": str(amount),
                },
                now,
                reason=reason,
            )
        return adjustment_id

    def reverse_adjustment(
        self,
        entry_id: str,
        *,
        occurred_on: date,
        actor: str,
        reason: str,
    ) -> str:
        actor = self._required(actor, "actor")
        reason = self._required(reason, "reversal reason")
        reversal_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            original = connection.execute(
                "SELECT * FROM financial_adjustments WHERE id=?",
                (entry_id,),
            ).fetchone()
            if (
                original is None
                or original["status"] != "confirmed"
                or original["reversal_of"] is not None
            ):
                raise LedgerError("only confirmed adjustments can be reversed")
            exists = connection.execute(
                "SELECT 1 FROM financial_adjustments WHERE reversal_of=?",
                (entry_id,),
            ).fetchone()
            if exists is not None:
                raise LedgerError("adjustment already reversed")
            amount = -_money(Decimal(original["amount_cny"]))
            connection.execute(
                """
                INSERT INTO financial_adjustments(
                    id, adjustment_type, amount_cny, occurred_on, reason,
                    status, reversal_of, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'reversed', ?, ?, ?)
                """,
                (
                    reversal_id,
                    original["adjustment_type"],
                    str(amount),
                    occurred_on.isoformat(),
                    reason,
                    entry_id,
                    actor,
                    now,
                ),
            )
            original_cash = connection.execute(
                """
                SELECT id FROM cash_movements
                WHERE source_type='financial_adjustment' AND source_id=?
                  AND reversal_of IS NULL
                """,
                (entry_id,),
            ).fetchone()
            if original_cash is None:
                raise LedgerError("adjustment cash fact is missing")
            connection.execute(
                """
                INSERT INTO cash_movements(
                    id, occurred_on, movement_type, amount_cny,
                    source_type, source_id, reversal_of, created_at
                ) VALUES (
                    ?, ?, 'financial_adjustment_reversal', ?,
                    'financial_adjustment', ?, ?, ?
                )
                """,
                (
                    str(uuid.uuid4()),
                    occurred_on.isoformat(),
                    str(amount),
                    reversal_id,
                    original_cash["id"],
                    now,
                ),
            )
            self._audit_generic(
                connection,
                "financial_adjustment",
                entry_id,
                actor,
                {"reversal_id": reversal_id},
                now,
                action="reverse",
                before={"status": "confirmed"},
                reason=reason,
            )
            self._audit_generic(
                connection,
                "financial_adjustment",
                reversal_id,
                actor,
                {"status": "reversed", "reversal_of": entry_id},
                now,
                reason=reason,
            )
        return reversal_id

    def _reverse_entry(
        self,
        *,
        table: str,
        object_type: str,
        source_type: str,
        entry_id: str,
        actor: str,
        reason: str,
    ) -> None:
        actor = self._required(actor, "actor")
        reason = self._required(reason, "reversal reason")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None or row["status"] != "confirmed":
                raise LedgerError(
                    f"only confirmed {object_type} entries can be reversed"
                )
            original_cash = connection.execute(
                """
                SELECT id, occurred_on, movement_type, amount_cny
                FROM cash_movements
                WHERE source_type=? AND source_id=? AND reversal_of IS NULL
                """,
                (source_type, entry_id),
            ).fetchone()
            if original_cash is None:
                raise LedgerError("original cash fact is missing")
            already_reversed = connection.execute(
                "SELECT 1 FROM cash_movements WHERE reversal_of=?",
                (original_cash["id"],),
            ).fetchone()
            if already_reversed is not None:
                raise LedgerError(f"{object_type} entry already reversed")
            connection.execute(
                f"""
                UPDATE {table}
                SET status='reversed', reversal_reason=? WHERE id=?
                """,
                (reason, entry_id),
            )
            connection.execute(
                """
                INSERT INTO cash_movements(
                    id, occurred_on, movement_type, amount_cny,
                    source_type, source_id, reversal_of, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    original_cash["occurred_on"],
                    f"{original_cash['movement_type']}_reversal",
                    str(-_money(Decimal(original_cash["amount_cny"]))),
                    source_type,
                    entry_id,
                    original_cash["id"],
                    now,
                ),
            )
            self._audit_generic(
                connection,
                object_type,
                entry_id,
                actor,
                {"status": "reversed"},
                now,
                action="reverse",
                before={"status": "confirmed"},
                reason=reason,
            )

    @staticmethod
    def _required(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise LedgerError(f"{label} is required")
        return normalized

    @staticmethod
    def _cash(
        connection,
        occurred_on,
        movement_type: str,
        amount: Decimal,
        source_type: str,
        source_id: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO cash_movements(
                id, occurred_on, movement_type, amount_cny,
                source_type, source_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                occurred_on.isoformat(),
                movement_type,
                str(_money(amount)),
                source_type,
                source_id,
                now,
            ),
        )

    @staticmethod
    def _audit_generic(
        connection,
        object_type: str,
        object_id: str,
        actor: str,
        after: dict,
        now: str,
        *,
        action: str = "create",
        before: dict | None = None,
        reason: str | None = None,
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
                json.dumps(after, sort_keys=True),
                actor,
                reason,
                now,
            ),
        )

    @staticmethod
    def _audit(
        connection,
        object_id: str,
        action: str,
        actor: str,
        reason: str | None,
        before: dict | None,
        after: dict | None,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                id, object_type, object_id, action, before_json,
                after_json, actor, reason, created_at
            ) VALUES (?, 'expense', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                object_id,
                action,
                None if before is None else json.dumps(before, sort_keys=True),
                None if after is None else json.dumps(after, sort_keys=True),
                actor,
                reason,
                created_at,
            ),
        )

    # ------------------------------------------------------------------
    # 只读查询方法
    # ------------------------------------------------------------------

    def list_adjustments(
        self,
        *,
        adjustment_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[FinancialAdjustment, ...]:
        clauses: list[str] = []
        params: list[str] = []
        if adjustment_type is not None:
            clauses.append("adjustment_type=?")
            params.append(adjustment_type)
        if start_date is not None:
            clauses.append("occurred_on>=?")
            params.append(start_date.isoformat())
        if end_date is not None:
            clauses.append("occurred_on<=?")
            params.append(end_date.isoformat())
        query = "SELECT * FROM financial_adjustments"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY occurred_on DESC, created_at DESC, id DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            documents = connection.execute(
                """
                SELECT object_id, id FROM evidence_documents
                WHERE object_type='financial_adjustment'
                ORDER BY created_at, id
                """
            ).fetchall()
        evidence: dict[str, list[str]] = {}
        for document in documents:
            evidence.setdefault(str(document["object_id"]), []).append(
                str(document["id"])
            )
        return tuple(
            FinancialAdjustment(
                id=str(row["id"]),
                adjustment_type=str(row["adjustment_type"]),
                amount_cny=Decimal(row["amount_cny"]),
                occurred_on=date.fromisoformat(row["occurred_on"]),
                reason=str(row["reason"]),
                status=str(row["status"]),
                reversal_of=(
                    None
                    if row["reversal_of"] is None
                    else str(row["reversal_of"])
                ),
                created_by=str(row["created_by"]),
                created_at=str(row["created_at"]),
                evidence_ids=tuple(evidence.get(str(row["id"]), [])),
            )
            for row in rows
        )

    def list_ad_funding_entries(
        self,
        *,
        platform: str | None = None,
        store: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[dict, ...]:
        """查询广告资金流水。

        返回充值/赠送/退款记录，支持按平台、店铺、日期筛选。

        返回字段: id, platform, store, entry_type, amount_cny,
                  occurred_on, source, status
        """
        clauses: list[str] = []
        params: list = []
        if platform is not None:
            clauses.append("platform = ?")
            params.append(platform)
        if store is not None:
            clauses.append("store = ?")
            params.append(store)
        if start_date is not None:
            clauses.append("occurred_on >= ?")
            params.append(start_date.isoformat())
        if end_date is not None:
            clauses.append("occurred_on <= ?")
            params.append(end_date.isoformat())

        sql = (
            "SELECT id, platform, store, entry_type, amount_cny, "
            "occurred_on, source, status, created_by, reversal_reason "
            "FROM ad_funding_entries"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_on DESC"

        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            evidence = self._evidence_by_object(connection, "ad_funding")

        return tuple(
            {
                "id": row["id"],
                "platform": row["platform"],
                "store": row["store"],
                "entry_type": row["entry_type"],
                "amount_cny": Decimal(row["amount_cny"]),
                "occurred_on": date.fromisoformat(row["occurred_on"]),
                "source": row["source"],
                "status": row["status"],
                "created_by": row["created_by"],
                "reversal_reason": row["reversal_reason"],
                "evidence_ids": evidence.get(str(row["id"]), ()),
            }
            for row in rows
        )

    def list_expenses(
        self,
        *,
        category: str | None = None,
        fund_nature: str | None = None,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[dict, ...]:
        """查询费用列表。

        支持按类别、资金性质、状态、日期筛选。

        返回字段: id, occurred_on, category, amount_cny, payer,
                  fund_nature, status
        """
        clauses: list[str] = []
        params: list = []
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        if fund_nature is not None:
            clauses.append("fund_nature = ?")
            params.append(fund_nature)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if start_date is not None:
            clauses.append("occurred_on >= ?")
            params.append(start_date.isoformat())
        if end_date is not None:
            clauses.append("occurred_on <= ?")
            params.append(end_date.isoformat())

        sql = (
            "SELECT id, occurred_on, category, amount_cny, payer, "
            "fund_nature, status, reversal_reason, created_by "
            "FROM expense_entries"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_on DESC"

        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            evidence = self._evidence_by_object(connection, "expense")

        return tuple(
            {
                "id": row["id"],
                "occurred_on": date.fromisoformat(row["occurred_on"]),
                "category": row["category"],
                "amount_cny": Decimal(row["amount_cny"]),
                "payer": row["payer"],
                "fund_nature": row["fund_nature"],
                "status": row["status"],
                "created_by": row["created_by"],
                "reversal_reason": row["reversal_reason"],
                "evidence_ids": evidence.get(str(row["id"]), ()),
            }
            for row in rows
        )

    def list_capital_entries(
        self,
        *,
        partner: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[dict, ...]:
        """查询合伙人出资记录。

        返回每位合伙人的出资明细。

        返回字段: id, partner, entry_type, amount_cny, occurred_on, status
        """
        clauses: list[str] = []
        params: list = []
        if partner is not None:
            clauses.append("partner = ?")
            params.append(partner)
        if start_date is not None:
            clauses.append("occurred_on >= ?")
            params.append(start_date.isoformat())
        if end_date is not None:
            clauses.append("occurred_on <= ?")
            params.append(end_date.isoformat())

        sql = (
            "SELECT id, partner, entry_type, amount_cny, occurred_on, "
            "status, created_by, reversal_reason "
            "FROM capital_entries"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_on DESC"

        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            evidence = self._evidence_by_object(connection, "capital")

        return tuple(
            {
                "id": row["id"],
                "partner": row["partner"],
                "entry_type": row["entry_type"],
                "amount_cny": Decimal(row["amount_cny"]),
                "occurred_on": date.fromisoformat(row["occurred_on"]),
                "status": row["status"],
                "created_by": row["created_by"],
                "reversal_reason": row["reversal_reason"],
                "evidence_ids": evidence.get(str(row["id"]), ()),
            }
            for row in rows
        )

    def list_withdrawal_entries(self) -> tuple[dict, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, partner, amount_cny, occurred_on, purpose,
                       status, created_by, reversal_reason
                FROM withdrawal_entries
                ORDER BY occurred_on DESC, created_at DESC
                """
            ).fetchall()
            evidence = self._evidence_by_object(connection, "withdrawal")
        return tuple(
            {
                "id": row["id"],
                "partner": row["partner"],
                "amount_cny": Decimal(row["amount_cny"]),
                "occurred_on": date.fromisoformat(row["occurred_on"]),
                "purpose": row["purpose"],
                "status": row["status"],
                "created_by": row["created_by"],
                "reversal_reason": row["reversal_reason"],
                "evidence_ids": evidence.get(str(row["id"]), ()),
            }
            for row in rows
        )

    def list_review_order_costs(self) -> tuple[dict, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, platform, store, order_id, seller_sku,
                       goods_cost_cny, service_fee_cny, occurred_on,
                       status, created_by, reversal_reason
                FROM review_order_costs
                ORDER BY occurred_on DESC, created_at DESC
                """
            ).fetchall()
            evidence = self._evidence_by_object(
                connection, "review_order_cost"
            )
        return tuple(
            {
                "id": row["id"],
                "platform": row["platform"],
                "store": row["store"],
                "order_id": row["order_id"],
                "seller_sku": row["seller_sku"],
                "goods_cost_cny": Decimal(row["goods_cost_cny"]),
                "service_fee_cny": Decimal(row["service_fee_cny"]),
                "occurred_on": date.fromisoformat(row["occurred_on"]),
                "status": row["status"],
                "created_by": row["created_by"],
                "reversal_reason": row["reversal_reason"],
                "evidence_ids": evidence.get(str(row["id"]), ()),
            }
            for row in rows
        )

    @staticmethod
    def _evidence_by_object(connection, object_type: str) -> dict[str, tuple[str, ...]]:
        rows = connection.execute(
            """
            SELECT object_id, id FROM evidence_documents
            WHERE object_type=? ORDER BY created_at, id
            """,
            (object_type,),
        ).fetchall()
        collected: dict[str, list[str]] = {}
        for row in rows:
            collected.setdefault(str(row["object_id"]), []).append(str(row["id"]))
        return {key: tuple(value) for key, value in collected.items()}

    def get_cash_summary(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> dict:
        """查询指定期间的现金汇总。

        基于 cash_movements 表，统计指定日期范围内的现金流入流出。

        返回字段: total_income, total_expense, net_cash_flow, by_category
        """
        start = start_date.isoformat()
        end = end_date.isoformat()

        with self.database.connect() as connection:
            # 按 movement_type 分组统计
            rows = connection.execute(
                """
                SELECT movement_type,
                       SUM(CAST(amount_cny AS REAL)) AS total
                FROM cash_movements
                WHERE occurred_on >= ? AND occurred_on <= ?
                GROUP BY movement_type
                """,
                (start, end),
            ).fetchall()

        by_category: dict[str, Decimal] = {}
        total_income = Decimal(0)
        total_expense = Decimal(0)

        for row in rows:
            mtype = row["movement_type"]
            amount = _money(Decimal(str(row["total"])))
            by_category[mtype] = amount
            if amount >= 0:
                total_income += amount
            else:
                total_expense += amount

        return {
            "total_income": total_income,
            "total_expense": total_expense,
            "net_cash_flow": total_income + total_expense,
            "by_category": by_category,
        }

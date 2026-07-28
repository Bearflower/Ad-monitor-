from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from adwatch.ledger.models import ExpenseDraft, ExpenseEntry
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
        if not reason.strip():
            raise LedgerError("reversal reason is required")
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
                (reason.strip(), entry_id),
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
                reason.strip(),
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
        value = self._positive(amount)
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO capital_entries(
                    id, partner, entry_type, amount_original, currency,
                    rate_to_cny, amount_cny, occurred_on, status, created_at
                ) VALUES (?, ?, ?, ?, 'CNY', '1', ?, ?, 'confirmed', ?)
                """,
                (
                    entry_id,
                    partner,
                    entry_type,
                    str(value),
                    str(value),
                    occurred_on.isoformat(),
                    now,
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
        value = self._positive(amount)
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO withdrawal_entries(
                    id, partner, amount_original, currency, rate_to_cny,
                    amount_cny, occurred_on, purpose, status, created_at
                ) VALUES (?, ?, ?, 'CNY', '1', ?, ?, ?, 'confirmed', ?)
                """,
                (
                    entry_id,
                    partner,
                    str(value),
                    str(value),
                    occurred_on.isoformat(),
                    purpose,
                    now,
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
        value = self._positive(amount)
        entry_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ad_funding_entries(
                    id, platform, store, entry_type, amount_original,
                    currency, rate_to_cny, amount_cny, occurred_on,
                    source, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'CNY', '1', ?, ?, ?, 'confirmed', ?)
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
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
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
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                id, object_type, object_id, action,
                after_json, actor, created_at
            ) VALUES (?, ?, ?, 'create', ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                object_type,
                object_id,
                json.dumps(after, sort_keys=True),
                actor,
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

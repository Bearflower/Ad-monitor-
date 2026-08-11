from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from adwatch.storage.db import Database


class BalanceReconciliationError(ValueError):
    pass


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class BalanceSnapshot:
    id: str
    account_type: str
    account_name: str
    snapshot_date: date
    amount_cny: Decimal
    source: str
    platform: str
    store: str
    note: str
    created_by: str
    created_at: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BalanceReconciliation:
    id: str
    data_date: date
    ledger_cash_cny: Decimal
    cash_observed_cny: Decimal | None
    platform_observed_cny: Decimal | None
    bank_observed_cny: Decimal | None
    cash_difference_cny: Decimal | None
    platform_difference_cny: Decimal | None
    bank_difference_cny: Decimal | None
    status: str
    resolution_adjustment_id: str | None
    supersedes_id: str | None
    created_by: str
    created_at: str

    @property
    def snapshot_state(self) -> str:
        observed = (
            self.cash_observed_cny,
            self.platform_observed_cny,
            self.bank_observed_cny,
        )
        if all(value is None for value in observed):
            return "none"
        if any(value is None for value in observed):
            return "partial"
        return "complete"


class BalanceReconciliationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record_snapshot(
        self,
        *,
        account_type: str,
        account_name: str,
        snapshot_date: date,
        amount_cny: Decimal,
        source: str,
        actor: str,
        platform: str = "",
        store: str = "",
        note: str = "",
    ) -> BalanceSnapshot:
        if account_type not in {"cash", "platform", "bank"}:
            raise BalanceReconciliationError("invalid account type")
        account_name = self._required(account_name, "account name")
        source = self._required(source, "source")
        actor = self._required(actor, "actor")
        snapshot_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        amount = _money(amount_cny)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO balance_snapshots(
                    id, account_type, account_name, platform, store,
                    snapshot_date, amount_cny, source, note,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    account_type,
                    account_name,
                    platform.strip(),
                    store.strip(),
                    snapshot_date.isoformat(),
                    str(amount),
                    source,
                    note.strip(),
                    actor,
                    now,
                ),
            )
            self._audit(
                connection,
                "balance_snapshot",
                snapshot_id,
                "create",
                actor,
                note.strip() or None,
                None,
                {
                    "account_type": account_type,
                    "snapshot_date": snapshot_date.isoformat(),
                    "amount_cny": str(amount),
                },
                now,
            )
        return BalanceSnapshot(
            id=snapshot_id,
            account_type=account_type,
            account_name=account_name,
            snapshot_date=snapshot_date,
            amount_cny=amount,
            source=source,
            platform=platform.strip(),
            store=store.strip(),
            note=note.strip(),
            created_by=actor,
            created_at=now,
        )

    def reconcile(
        self, *, data_date: date, actor: str
    ) -> BalanceReconciliation:
        actor = self._required(actor, "actor")
        reconciliation_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            ledger, observed = self._balance_facts(connection, data_date)
            differences = {
                key: (
                    None
                    if value is None
                    else _money(value - ledger)
                )
                for key, value in observed.items()
            }
            if any(value is None for value in observed.values()):
                status = "pending_data"
            elif any(value != Decimal("0.00") for value in differences.values()):
                status = "open"
            else:
                status = "resolved"
            result = BalanceReconciliation(
                id=reconciliation_id,
                data_date=data_date,
                ledger_cash_cny=ledger,
                cash_observed_cny=observed["cash"],
                platform_observed_cny=observed["platform"],
                bank_observed_cny=observed["bank"],
                cash_difference_cny=differences["cash"],
                platform_difference_cny=differences["platform"],
                bank_difference_cny=differences["bank"],
                status=status,
                resolution_adjustment_id=None,
                supersedes_id=None,
                created_by=actor,
                created_at=now,
            )
            self._insert_reconciliation(connection, result)
            self._audit(
                connection,
                "balance_reconciliation",
                reconciliation_id,
                "create",
                actor,
                None,
                None,
                {"status": status, "snapshot_state": result.snapshot_state},
                now,
            )
        return result

    def resolve(
        self,
        reconciliation_id: str,
        *,
        adjustment_id: str,
        actor: str,
    ) -> BalanceReconciliation:
        actor = self._required(actor, "actor")
        new_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM balance_reconciliations WHERE id=?",
                (reconciliation_id,),
            ).fetchone()
            if row is None or row["status"] != "open":
                raise BalanceReconciliationError(
                    "only open reconciliations can be resolved"
                )
            exists = connection.execute(
                "SELECT 1 FROM balance_reconciliations WHERE supersedes_id=?",
                (reconciliation_id,),
            ).fetchone()
            if exists is not None:
                raise BalanceReconciliationError(
                    "reconciliation already resolved"
                )
            adjustment = connection.execute(
                """
                SELECT * FROM financial_adjustments
                WHERE id=? AND adjustment_type IN ('cash', 'balance')
                  AND status='confirmed' AND reversal_of IS NULL
                """,
                (adjustment_id,),
            ).fetchone()
            if adjustment is None:
                raise BalanceReconciliationError(
                    "confirmed balance adjustment is required"
                )
            resolved = BalanceReconciliation(
                id=new_id,
                data_date=date.fromisoformat(row["data_date"]),
                ledger_cash_cny=Decimal(row["ledger_cash_cny"]),
                cash_observed_cny=self._optional_money(row["cash_observed_cny"]),
                platform_observed_cny=self._optional_money(
                    row["platform_observed_cny"]
                ),
                bank_observed_cny=self._optional_money(row["bank_observed_cny"]),
                cash_difference_cny=self._optional_money(
                    row["cash_difference_cny"]
                ),
                platform_difference_cny=self._optional_money(
                    row["platform_difference_cny"]
                ),
                bank_difference_cny=self._optional_money(
                    row["bank_difference_cny"]
                ),
                status="resolved",
                resolution_adjustment_id=adjustment_id,
                supersedes_id=reconciliation_id,
                created_by=actor,
                created_at=now,
            )
            self._insert_reconciliation(connection, resolved)
            self._audit(
                connection,
                "balance_reconciliation",
                new_id,
                "resolve",
                actor,
                str(adjustment["reason"]),
                {"status": "open", "supersedes_id": reconciliation_id},
                {
                    "status": "resolved",
                    "resolution_adjustment_id": adjustment_id,
                },
                now,
            )
        return resolved

    def list_reconciliations(
        self, *, data_date: date | None = None
    ) -> tuple[BalanceReconciliation, ...]:
        query = "SELECT * FROM balance_reconciliations"
        params: tuple[str, ...] = ()
        if data_date is not None:
            query += " WHERE data_date=?"
            params = (data_date.isoformat(),)
        query += " ORDER BY data_date DESC, created_at DESC, id DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def list_snapshots(
        self, *, through: date | None = None
    ) -> tuple[BalanceSnapshot, ...]:
        query = "SELECT * FROM balance_snapshots"
        params: tuple[str, ...] = ()
        if through is not None:
            query += " WHERE snapshot_date<=?"
            params = (through.isoformat(),)
        query += " ORDER BY snapshot_date DESC, created_at DESC, id DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT object_id, id FROM evidence_documents
                WHERE object_type='balance_snapshot'
                ORDER BY created_at, id
                """
            ).fetchall()
        evidence: dict[str, list[str]] = {}
        for row in evidence_rows:
            evidence.setdefault(str(row["object_id"]), []).append(
                str(row["id"])
            )
        return tuple(
            BalanceSnapshot(
                id=str(row["id"]),
                account_type=str(row["account_type"]),
                account_name=str(row["account_name"]),
                snapshot_date=date.fromisoformat(row["snapshot_date"]),
                amount_cny=Decimal(row["amount_cny"]),
                source=str(row["source"]),
                platform=str(row["platform"]),
                store=str(row["store"]),
                note=str(row["note"]),
                created_by=str(row["created_by"]),
                created_at=str(row["created_at"]),
                evidence_ids=tuple(evidence.get(str(row["id"]), [])),
            )
            for row in rows
        )

    @staticmethod
    def _balance_facts(connection, data_date: date):
        cash_rows = connection.execute(
            """
            SELECT amount_cny FROM cash_movements WHERE occurred_on<=?
            """,
            (data_date.isoformat(),),
        ).fetchall()
        ledger = _money(
            sum(
                (Decimal(row["amount_cny"]) for row in cash_rows),
                Decimal(0),
            )
        )
        snapshot_rows = connection.execute(
            """
            SELECT * FROM (
                SELECT snapshot.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY account_type, account_name,
                                        platform, store
                           ORDER BY snapshot_date DESC, created_at DESC, id DESC
                       ) AS position
                FROM balance_snapshots AS snapshot
                WHERE snapshot_date<=?
            ) WHERE position=1
            """,
            (data_date.isoformat(),),
        ).fetchall()
        observed: dict[str, Decimal | None] = {}
        for account_type in ("cash", "platform", "bank"):
            amounts = [
                Decimal(row["amount_cny"])
                for row in snapshot_rows
                if row["account_type"] == account_type
            ]
            observed[account_type] = (
                None if not amounts else _money(sum(amounts, Decimal(0)))
            )
        return ledger, observed

    @staticmethod
    def _insert_reconciliation(connection, item: BalanceReconciliation) -> None:
        connection.execute(
            """
            INSERT INTO balance_reconciliations(
                id, data_date, ledger_cash_cny, cash_observed_cny,
                platform_observed_cny, bank_observed_cny,
                cash_difference_cny, platform_difference_cny,
                bank_difference_cny, status, resolution_adjustment_id,
                supersedes_id, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.data_date.isoformat(),
                str(item.ledger_cash_cny),
                BalanceReconciliationService._optional_text(
                    item.cash_observed_cny
                ),
                BalanceReconciliationService._optional_text(
                    item.platform_observed_cny
                ),
                BalanceReconciliationService._optional_text(
                    item.bank_observed_cny
                ),
                BalanceReconciliationService._optional_text(
                    item.cash_difference_cny
                ),
                BalanceReconciliationService._optional_text(
                    item.platform_difference_cny
                ),
                BalanceReconciliationService._optional_text(
                    item.bank_difference_cny
                ),
                item.status,
                item.resolution_adjustment_id,
                item.supersedes_id,
                item.created_by,
                item.created_at,
            ),
        )

    @staticmethod
    def _from_row(row) -> BalanceReconciliation:
        return BalanceReconciliation(
            id=str(row["id"]),
            data_date=date.fromisoformat(row["data_date"]),
            ledger_cash_cny=Decimal(row["ledger_cash_cny"]),
            cash_observed_cny=BalanceReconciliationService._optional_money(
                row["cash_observed_cny"]
            ),
            platform_observed_cny=BalanceReconciliationService._optional_money(
                row["platform_observed_cny"]
            ),
            bank_observed_cny=BalanceReconciliationService._optional_money(
                row["bank_observed_cny"]
            ),
            cash_difference_cny=BalanceReconciliationService._optional_money(
                row["cash_difference_cny"]
            ),
            platform_difference_cny=BalanceReconciliationService._optional_money(
                row["platform_difference_cny"]
            ),
            bank_difference_cny=BalanceReconciliationService._optional_money(
                row["bank_difference_cny"]
            ),
            status=str(row["status"]),
            resolution_adjustment_id=(
                None
                if row["resolution_adjustment_id"] is None
                else str(row["resolution_adjustment_id"])
            ),
            supersedes_id=(
                None
                if row["supersedes_id"] is None
                else str(row["supersedes_id"])
            ),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _optional_money(value) -> Decimal | None:
        return None if value is None else Decimal(value)

    @staticmethod
    def _optional_text(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _required(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise BalanceReconciliationError(f"{label} is required")
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

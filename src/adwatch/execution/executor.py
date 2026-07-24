from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from adwatch.storage.db import Database


class ExecutionError(RuntimeError):
    pass


class ExecutionBackend(Protocol):
    def read_current(self, recommendation: dict) -> dict: ...
    def execute(self, recommendation: dict) -> dict: ...
    def capture(self, label: str) -> str: ...


@dataclass(frozen=True)
class ExecutionResult:
    audit_id: str
    status: str


class SafeExecutor:
    BLOCKED_ACTIONS = {
        "delete",
        "delete_campaign",
        "modify_account",
        "modify_store",
        "modify_security",
        "create_large_campaign",
    }

    def __init__(self, database: Database, backend: ExecutionBackend) -> None:
        self.database = database
        self.backend = backend

    def execute(
        self,
        approval_id: str,
        *,
        idempotency_key: str,
        expected_before: dict,
    ) -> ExecutionResult:
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM execution_audits WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone():
                raise ExecutionError("idempotency key already used")
            row = connection.execute(
                """
                SELECT a.status approval_status, a.expires_at, r.*
                FROM approvals a
                JOIN recommendations r ON r.id=a.recommendation_id
                WHERE a.id=?
                """,
                (approval_id,),
            ).fetchone()
            circuit = connection.execute(
                "SELECT is_open FROM circuit_state WHERE id=1"
            ).fetchone()
        if row is None or row["approval_status"] != "approved":
            raise ExecutionError("approval is not approved")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(
            timezone.utc
        ):
            raise ExecutionError("approval has expired")
        if circuit and circuit["is_open"]:
            raise ExecutionError("write circuit is open")
        if row["action"] in self.BLOCKED_ACTIONS:
            raise ExecutionError("action is permanently blocked")
        if (
            row["action"] == "increase_budget"
            and Decimal(row["change_ratio"] or "0") > Decimal("0.50")
        ):
            raise ExecutionError("budget increase above 50% is blocked")
        recommendation = dict(row)
        current = self.backend.read_current(recommendation)
        if current != expected_before:
            raise ExecutionError("current state drift detected")

        now = datetime.now(timezone.utc).isoformat()
        audit_id = str(uuid.uuid4())
        before_screenshot = self.backend.capture(f"{audit_id}-before")
        after = self.backend.execute(recommendation)
        after_screenshot = self.backend.capture(f"{audit_id}-after")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO execution_audits(
                    id, approval_id, action, before_json, after_json,
                    before_screenshot, after_screenshot, status,
                    idempotency_key, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?)
                """,
                (
                    audit_id,
                    approval_id,
                    row["action"],
                    json.dumps(current, sort_keys=True),
                    json.dumps(after, sort_keys=True),
                    before_screenshot,
                    after_screenshot,
                    idempotency_key,
                    now,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.execute(
                "UPDATE recommendations SET status='executed' WHERE id=?",
                (row["id"],),
            )
        return ExecutionResult(audit_id=audit_id, status="succeeded")

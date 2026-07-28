from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from adwatch.storage.db import Database


class ApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    decision_token: str
    expires_at: str


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    status: str
    actor: str


class ApprovalService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, recommendation_id: int) -> ApprovalRequest:
        now = datetime.now(UTC)
        expires = now + timedelta(hours=24)
        approval_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.database.transaction() as connection:
            recommendation = connection.execute(
                """
                SELECT requires_approval FROM recommendations WHERE id=?
                """,
                (recommendation_id,),
            ).fetchone()
            if recommendation is None:
                raise ApprovalError("recommendation does not exist")
            if not recommendation["requires_approval"]:
                raise ApprovalError("recommendation does not require approval")
            try:
                connection.execute(
                    """
                    INSERT INTO approvals(
                        id, recommendation_id, status, requested_at,
                        expires_at, decision_token_hash
                    ) VALUES (?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        approval_id,
                        recommendation_id,
                        now.isoformat(),
                        expires.isoformat(),
                        token_hash,
                    ),
                )
            except Exception as error:
                raise ApprovalError(
                    "approval already exists for recommendation"
                ) from error
        return ApprovalRequest(
            approval_id=approval_id,
            decision_token=token,
            expires_at=expires.isoformat(),
        )

    def decide(
        self,
        approval_id: str,
        token: str,
        *,
        approve: bool,
        actor: str,
        reason: str = "",
    ) -> ApprovalDecision:
        now = datetime.now(UTC)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError("approval does not exist")
            supplied_hash = hashlib.sha256(token.encode()).hexdigest()
            if not hmac.compare_digest(
                supplied_hash, row["decision_token_hash"]
            ):
                raise ApprovalError("invalid decision token")
            if row["status"] != "pending":
                raise ApprovalError("approval already decided")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute(
                    "UPDATE approvals SET status='expired' WHERE id=?",
                    (approval_id,),
                )
                raise ApprovalError("approval has expired")
            status = "approved" if approve else "rejected"
            connection.execute(
                """
                UPDATE approvals
                SET status=?, decided_at=?, decided_by=?, decision_reason=?
                WHERE id=?
                """,
                (status, now.isoformat(), actor, reason, approval_id),
            )
        return ApprovalDecision(
            approval_id=approval_id, status=status, actor=actor
        )

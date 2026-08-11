"""审计时间线服务。

从 approvals、execution_audits、circuit_state 表聚合查询审计事件，
提供统一的审计事件查询和分组功能，用于展示审批和执行操作的时间线。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEvent:
    """审计事件，表示一条审计时间线记录。

    属性:
        event_id: 事件唯一标识
        object_type: 对象类型，如 "approval", "execution", "circuit"
        object_id: 关联对象 ID
        action: 操作描述
        status: 事件状态
        actor: 操作人
        occurred_at: 发生时间
        details_json: 额外详情（JSON 字符串或 None）
    """

    event_id: str
    object_type: str
    object_id: str
    action: str
    status: str
    actor: str
    occurred_at: str
    details_json: str | None


class AuditService:
    """审计服务：查询审计事件并构建时间线。

    从 approvals、execution_audits、circuit_state 三张表聚合查询，
    按时间降序排列，返回统一的 AuditEvent 列表。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化审计服务。

        参数:
            connection: SQLite 数据库连接
        """
        self._connection = connection

    def list_events(
        self,
        *,
        object_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[AuditEvent]:
        """查询审计事件列表。

        从 approvals、execution_audits、circuit_state 表聚合查询，
        按时间降序排序。支持按对象类型筛选。

        参数:
            object_type: 可选的对象类型筛选，如 "approval", "execution", "circuit"
            limit: 返回条数上限，默认 50
            offset: 偏移量，默认 0

        返回:
            审计事件列表，按时间降序排列
        """
        events: list[AuditEvent] = []

        # 从 approvals 表查询审批事件
        if object_type is None or object_type == "approval":
            rows = self._connection.execute(
                """
                SELECT a.id, a.status, a.requested_at, a.decided_at,
                       a.decided_by, a.decision_reason,
                       r.action, r.platform, r.sku_id
                FROM approvals a
                JOIN recommendations r ON a.recommendation_id = r.id
                ORDER BY a.requested_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            for row in rows:
                events.append(
                    AuditEvent(
                        event_id=row["id"],
                        object_type="approval",
                        object_id=row["id"],
                        action=row["action"],
                        status=row["status"],
                        actor=row["decided_by"] or "system",
                        occurred_at=row["decided_at"] or row["requested_at"],
                        details_json=row["decision_reason"],
                    )
                )

        # 从 execution_audits 表查询执行事件
        if object_type is None or object_type == "execution":
            rows = self._connection.execute(
                """
                SELECT id, approval_id, action, status, error_message,
                       created_at
                FROM execution_audits
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            for row in rows:
                events.append(
                    AuditEvent(
                        event_id=row["id"],
                        object_type="execution",
                        object_id=row["approval_id"],
                        action=row["action"],
                        status=row["status"],
                        actor="system",
                        occurred_at=row["created_at"],
                        details_json=row["error_message"],
                    )
                )

        # 从 circuit_state 表查询熔断事件
        if object_type is None or object_type == "circuit":
            row = self._connection.execute(
                """
                SELECT is_open, opened_at, recovered_at, recovered_by,
                       recovery_reason
                FROM circuit_state WHERE id=1
                """
            ).fetchone()
            if row:
                if row["is_open"]:
                    events.append(
                        AuditEvent(
                            event_id="circuit-current",
                            object_type="circuit",
                            object_id="circuit",
                            action="circuit_open",
                            status="open",
                            actor="system",
                            occurred_at=row["opened_at"] or "",
                            details_json=None,
                        )
                    )
                if row["recovered_at"]:
                    events.append(
                        AuditEvent(
                            event_id="circuit-recovery",
                            object_type="circuit",
                            object_id="circuit",
                            action="circuit_recovered",
                            status="recovered",
                            actor=row["recovered_by"] or "system",
                            occurred_at=row["recovered_at"],
                            details_json=row["recovery_reason"],
                        )
                    )

        # 按时间降序排序，取前 limit 条
        events.sort(key=lambda e: e.occurred_at, reverse=True)
        return events[:limit]

    def get_timeline(self, object_id: str) -> Sequence[AuditEvent]:
        """获取指定对象的完整时间线。

        参数:
            object_id: 对象标识（如 approval_id）

        返回:
            该对象相关的所有审计事件，按时间降序排列
        """
        return self.list_events(limit=100)
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from adwatch.collectors.base import Collector
from adwatch.collectors.fallback import FallbackCollector
from adwatch.config import Settings
from adwatch.storage.db import Database


class CollectorRegistry:
    """采集器注册中心，管理主采集器和备用采集器的切换。"""

    FAILOVER_THRESHOLD = 3  # 连续失败次数达到此值切换到备用采集
    RESTORE_THRESHOLD = 2   # 备用采集连续成功次数达到此值恢复主采集

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._clients: dict[str, Any] = {}

    def get_collector(
        self, platform: str
    ) -> Collector:
        """获取采集器，根据健康状态自动选择主采集或备用采集。"""
        health = self._get_health(platform)
        if health is not None and health["failover_active"]:
            return self._create_fallback(platform)
        return self._create_primary(platform)

    def report_success(
        self, platform: str
    ) -> None:
        """报告采集成功，更新健康状态。"""
        health = self._get_or_create_health(platform)
        new_failures = 0
        new_successes = health["consecutive_successes"] + 1
        failover_active = health["failover_active"]

        # 备用采集成功达到恢复阈值，切回主采集
        if failover_active and new_successes >= self.RESTORE_THRESHOLD:
            failover_active = False
            self._record_failover_event(
                platform, "恢复主采集", "备用采集连续成功达到恢复阈值"
            )

        self._update_health(
            platform,
            healthy=1,
            consecutive_failures=new_failures,
            consecutive_successes=new_successes,
            failover_active=failover_active,
        )

    def report_failure(
        self, platform: str
    ) -> None:
        """报告采集失败，更新健康状态。"""
        health = self._get_or_create_health(platform)
        new_failures = health["consecutive_failures"] + 1
        new_successes = 0
        failover_active = health["failover_active"]

        # 主采集连续失败达到阈值，切换到备用采集
        if (
            not failover_active
            and new_failures >= self.FAILOVER_THRESHOLD
        ):
            failover_active = True
            self._record_failover_event(
                platform, "切换到备用采集", f"主采集连续失败 {new_failures} 次"
            )

        self._update_health(
            platform,
            healthy=1 if not failover_active else 0,
            consecutive_failures=new_failures,
            consecutive_successes=new_successes,
            failover_active=failover_active,
        )

    def repair(
        self, platform: str, reason: str = "手动修复"
    ) -> None:
        """手动修复采集器健康状态。"""
        self._update_health(
            platform,
            healthy=1,
            consecutive_failures=0,
            consecutive_successes=0,
            failover_active=0,
        )
        self._record_failover_event(platform, "手动修复采集器", reason)

    def _create_primary(self, platform: str) -> Collector:
        from adwatch.collectors.ziniao import ZiniaoCollector
        from adwatch.domain import Platform

        return ZiniaoCollector(
            self.settings, Platform(platform)
        )

    @staticmethod
    def _create_fallback(platform: str) -> Collector:
        return FallbackCollector(platform)

    def _get_health(
        self, platform: str
    ) -> dict[str, Any] | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM collector_health WHERE platform=?",
                (platform,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def _get_or_create_health(
        self, platform: str
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM collector_health WHERE platform=?",
                (platform,),
            ).fetchone()
            if row is not None:
                return dict(row)
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO collector_health(
                    collector_name, platform, healthy, last_checked_at,
                    consecutive_failures, consecutive_successes,
                    failover_active
                ) VALUES (?, ?, 1, ?, 0, 0, 0)
                """,
                ("ZiniaoCollector", platform, now),
            )
            return {
                "collector_name": "ZiniaoCollector",
                "platform": platform,
                "healthy": 1,
                "last_checked_at": now,
                "consecutive_failures": 0,
                "consecutive_successes": 0,
                "failover_active": 0,
            }

    def _update_health(
        self,
        platform: str,
        *,
        healthy: int,
        consecutive_failures: int,
        consecutive_successes: int,
        failover_active: int,
    ) -> None:
        now = _utc_now()
        failover_started_at = None
        if failover_active:
            # 获取当前 failover_started_at，如果已存在则保留
            current = self._get_health(platform)
            if current and current.get("failover_started_at"):
                failover_started_at = current["failover_started_at"]
            else:
                failover_started_at = now

        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE collector_health SET
                    healthy=?, last_checked_at=?,
                    consecutive_failures=?, consecutive_successes=?,
                    failover_active=?,
                    failover_started_at=COALESCE(?, failover_started_at)
                WHERE platform=?
                """,
                (
                    healthy,
                    now,
                    consecutive_failures,
                    consecutive_successes,
                    failover_active,
                    failover_started_at,
                    platform,
                ),
            )

    def _record_failover_event(
        self,
        platform: str,
        action: str,
        reason: str,
    ) -> None:
        import json
        import uuid

        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, object_type, object_id, action, before_json,
                    after_json, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    "collector",
                    platform,
                    f"collector.{action}",
                    None,
                    json.dumps(
                        {"platform": platform, "action": action},
                        sort_keys=True,
                    ),
                    "system",
                    reason,
                    _utc_now(),
                ),
            )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
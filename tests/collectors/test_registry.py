"""测试 CollectorRegistry 健康管理与采集器切换逻辑。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adwatch.collectors.fallback import FallbackCollector
from adwatch.collectors.registry import CollectorRegistry
from adwatch.config import Settings
from adwatch.storage.db import Database

# ---------------------------------------------------------------------------
# 辅助函数：直接查询数据库表，不依赖注册中心暴露的方法
# ---------------------------------------------------------------------------


def _health_row(database: Database, platform: str) -> dict | None:
    """获取 collector_health 表的一行记录，不存在时返回 None。"""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM collector_health WHERE platform=?",
            (platform,),
        ).fetchone()
        return dict(row) if row else None


def _audit_events(database: Database, platform: str) -> list[dict]:
    """获取指定平台的 audit_events 记录，按创建时间升序。"""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM audit_events WHERE object_id=? ORDER BY created_at",
            (platform,),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def database(tmp_path) -> Database:
    """创建临时 SQLite 数据库并执行全部迁移。"""
    db = Database(tmp_path / "adwatch.sqlite3")
    db.migrate()
    return db


@pytest.fixture
def settings(tmp_path) -> Settings:
    """创建最小配置，仅指定数据目录。"""
    return Settings(data_dir=tmp_path)


@pytest.fixture
def registry(database: Database, settings: Settings) -> CollectorRegistry:
    """创建 CollectorRegistry 实例，同时 mock ZiniaoCollector 避免真实紫鸟连接。

    mock 在整个测试期间有效，确保 _create_primary() 不会触发真实 Bridge 调用。
    """
    with patch("adwatch.collectors.ziniao.ZiniaoCollector"):
        yield CollectorRegistry(database, settings)


# ===================================================================
# 1. 健康状态与切换
# ===================================================================


class TestFailoverState:
    """验证故障切换和恢复的核心逻辑。"""

    def test_initial_state_uses_primary_collector(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """初始状态使用主采集器，failover_active=False。"""
        # 刚创建的 registry 没有健康记录，get_collector 应返回主采集器
        collector = registry.get_collector("shopee")
        assert not isinstance(collector, FallbackCollector)

        # 数据库中尚无健康记录，隐含 failover_active=False
        health = _health_row(database, "shopee")
        assert health is None

    def test_three_failures_triggers_failover(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """连续 3 次失败后切换到备用采集（检查 failover_active=True）。"""
        platform = "shopee"

        for _ in range(3):
            registry.report_failure(platform)

        health = _health_row(database, platform)
        assert health is not None
        assert health["failover_active"] == 1
        assert health["consecutive_failures"] == 3
        assert health["consecutive_successes"] == 0

        # get_collector 应返回备用采集器
        collector = registry.get_collector(platform)
        assert isinstance(collector, FallbackCollector)

    def test_two_successes_restores_primary(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """备用采集连续成功 2 次后恢复主采集（检查 failover_active=False）。"""
        platform = "shopee"

        # 先触发故障切换
        for _ in range(3):
            registry.report_failure(platform)

        health = _health_row(database, platform)
        assert health["failover_active"] == 1

        # 报告 2 次成功
        registry.report_success(platform)
        registry.report_success(platform)

        health = _health_row(database, platform)
        assert health is not None
        assert health["failover_active"] == 0
        assert health["consecutive_successes"] == 2
        assert health["consecutive_failures"] == 0

        # get_collector 应返回主采集器
        collector = registry.get_collector(platform)
        assert not isinstance(collector, FallbackCollector)

    def test_failover_does_not_trigger_before_threshold(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """连续 2 次失败不触发切换。"""
        platform = "shopee"

        for _ in range(2):
            registry.report_failure(platform)

        health = _health_row(database, platform)
        assert health is not None
        assert health["failover_active"] == 0
        assert health["consecutive_failures"] == 2

        # get_collector 仍返回主采集器
        collector = registry.get_collector(platform)
        assert not isinstance(collector, FallbackCollector)


# ===================================================================
# 2. report_success / report_failure 健康状态更新
# ===================================================================


class TestHealthStateUpdate:
    """验证 report_success 和 report_failure 对计数器的精确影响。"""

    def test_report_success_resets_failures(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """报告成功后 consecutive_failures 重置为 0。"""
        platform = "shopee"

        # 先报告 2 次失败
        registry.report_failure(platform)
        registry.report_failure(platform)
        assert _health_row(database, platform)["consecutive_failures"] == 2

        # 报告成功
        registry.report_success(platform)

        health = _health_row(database, platform)
        assert health["consecutive_failures"] == 0

    def test_report_failure_increments_counter(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """报告失败后 consecutive_failures 递增。"""
        platform = "shopee"

        registry.report_failure(platform)
        assert _health_row(database, platform)["consecutive_failures"] == 1

        registry.report_failure(platform)
        assert _health_row(database, platform)["consecutive_failures"] == 2

    def test_report_success_increments_successes(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """报告成功后 consecutive_successes 递增。"""
        platform = "shopee"

        registry.report_success(platform)
        assert _health_row(database, platform)["consecutive_successes"] == 1

        registry.report_success(platform)
        assert _health_row(database, platform)["consecutive_successes"] == 2

    def test_report_failure_resets_successes(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """报告失败后 consecutive_successes 重置为 0。"""
        platform = "shopee"

        # 先积累 2 次成功
        registry.report_success(platform)
        registry.report_success(platform)
        assert _health_row(database, platform)["consecutive_successes"] == 2

        # 一次失败打断
        registry.report_failure(platform)

        health = _health_row(database, platform)
        assert health["consecutive_successes"] == 0


# ===================================================================
# 3. 手动修复
# ===================================================================


class TestRepair:
    """验证手动修复功能。"""

    def test_repair_resets_health(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """手动修复后健康状态重置：failover_active=False，consecutive_failures=0。"""
        platform = "shopee"

        # 先触发故障切换
        for _ in range(3):
            registry.report_failure(platform)
        assert _health_row(database, platform)["failover_active"] == 1
        assert _health_row(database, platform)["consecutive_failures"] == 3

        # 手动修复
        registry.repair(platform, reason="人工确认网络已恢复")

        health = _health_row(database, platform)
        assert health is not None
        assert health["failover_active"] == 0
        assert health["consecutive_failures"] == 0
        assert health["consecutive_successes"] == 0
        assert health["healthy"] == 1

        # 修复后 get_collector 应返回主采集器
        collector = registry.get_collector(platform)
        assert not isinstance(collector, FallbackCollector)


# ===================================================================
# 4. 切换事件记录
# ===================================================================


class TestAuditEvents:
    """验证故障切换和恢复时写入 audit_events 表。"""

    def test_failover_records_audit_event(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """切换时写入 audit_events 表。"""
        platform = "shopee"

        # 触发故障切换
        for _ in range(3):
            registry.report_failure(platform)

        events = _audit_events(database, platform)
        # 应只有一条切换事件
        assert len(events) == 1
        event = events[0]
        assert event["object_type"] == "collector"
        assert event["object_id"] == platform
        assert event["action"] == "collector.切换到备用采集"
        assert event["actor"] == "system"
        assert "主采集连续失败" in event["reason"]
        assert event["before_json"] is None
        assert event["after_json"] is not None

    def test_restore_records_audit_event(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """恢复时写入 audit_events 表。"""
        platform = "shopee"

        # 触发故障切换
        for _ in range(3):
            registry.report_failure(platform)

        # 恢复主采集（2 次成功）
        registry.report_success(platform)
        registry.report_success(platform)

        events = _audit_events(database, platform)
        # 应有 2 条事件：切换 + 恢复
        assert len(events) == 2
        restore_event = events[1]
        assert restore_event["object_type"] == "collector"
        assert restore_event["object_id"] == platform
        assert restore_event["action"] == "collector.恢复主采集"
        assert restore_event["actor"] == "system"
        assert "备用采集连续成功达到恢复阈值" in restore_event["reason"]


# ===================================================================
# 5. get_collector 返回类型
# ===================================================================


class TestGetCollectorReturnType:
    """验证 get_collector 在不同场景下返回正确的采集器类型。"""

    def test_get_collector_returns_primary_when_healthy(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """健康时返回主采集器。"""
        platform = "shopee"

        # 报告一次成功，创建健康记录（failover_active=False）
        registry.report_success(platform)

        collector = registry.get_collector(platform)
        assert not isinstance(collector, FallbackCollector)

    def test_get_collector_returns_fallback_when_failover(
        self, registry: CollectorRegistry, database: Database
    ) -> None:
        """故障切换后返回备用采集器。"""
        platform = "shopee"

        # 触发故障切换
        for _ in range(3):
            registry.report_failure(platform)

        collector = registry.get_collector(platform)
        assert isinstance(collector, FallbackCollector)
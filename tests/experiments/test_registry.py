"""adwatch.experiments.registry 模块集成测试。

测试 ExperimentRepository 的 CRUD 操作、状态转换、变体管理、窗口记录。
使用临时 SQLite 数据库，每次测试独立运行。
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from adwatch.experiments.models import (
    ExperimentGuardrailBreach,
    ExperimentStatus,
    VariantStatus,
    VariantType,
    _utc_now,
)
from adwatch.experiments.registry import ExperimentRepository
from adwatch.storage.db import Database

# ============================================================================
# 测试夹具
# ============================================================================


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """创建临时数据库并执行迁移。"""
    database = Database(tmp_path / "test_experiments.db")
    database.migrate()
    return database


@pytest.fixture
def repo(db: Database) -> ExperimentRepository:
    """创建 ExperimentRepository 实例。"""
    return ExperimentRepository(database=db)


@pytest.fixture
def sample_experiment(repo: ExperimentRepository) -> dict[str, Any]:
    """创建实验所需的参数，用于多个测试复用。"""
    return {
        "name": "测试实验-调整出价策略",
        "description": "测试将目标ROAS从3.0调整到2.5对转化量的影响",
        "platform": "tiktok",
        "store": "store_shopify_001",
        "starts_on": date(2026, 8, 1),
        "ends_on": date(2026, 8, 14),
        "guardrail_config_json": (
            '{"max_daily_spend": "500.00", "max_roas_drop_pct": 0.3}'
        ),
        "created_by": "test-user",
        "notes": "这是备注",
    }


# ============================================================================
# CRUD 操作
# ============================================================================


class TestCreateExperiment:
    """创建实验测试。"""

    def test_create_experiment(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """创建实验后返回 Experiment 对象，包含所有字段。"""
        exp = repo.create_experiment(**sample_experiment)

        # 验证返回对象类型
        assert exp is not None
        assert exp.id is not None
        # id 应为合法 UUID
        uuid.UUID(exp.id)

        # 验证所有字段正确写入
        assert exp.name == sample_experiment["name"]
        assert exp.description == sample_experiment["description"]
        assert exp.platform == sample_experiment["platform"]
        assert exp.store == sample_experiment["store"]
        assert exp.starts_on == sample_experiment["starts_on"]
        assert exp.ends_on == sample_experiment["ends_on"]
        assert exp.guardrail_config_json == sample_experiment["guardrail_config_json"]
        assert exp.created_by == sample_experiment["created_by"]
        assert exp.notes == sample_experiment["notes"]

        # 默认状态为 DRAFT
        assert exp.status == ExperimentStatus.DRAFT
        # created_at 不为空
        assert exp.created_at is not None


class TestGetExperiment:
    """查询实验测试。"""

    def test_get_experiment(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """按 ID 查询实验，返回正确对象。"""
        created = repo.create_experiment(**sample_experiment)
        fetched = repo.get_experiment(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == created.name
        assert fetched.platform == created.platform
        assert fetched.store == created.store
        assert fetched.status == created.status

    def test_get_experiment_not_found(
        self, repo: ExperimentRepository,
    ) -> None:
        """查询不存在的实验返回 None。"""
        result = repo.get_experiment("non-existent-id")
        assert result is None


class TestListExperiments:
    """列出实验测试。"""

    def _create_experiments(
        self, repo: ExperimentRepository, count: int = 3
    ) -> list[Any]:
        """辅助：创建多个实验用于列表测试。"""
        experiments = []
        for i in range(count):
            exp = repo.create_experiment(
                name=f"实验-{i}",
                description=f"描述-{i}",
                platform="tiktok" if i % 2 == 0 else "shopee",
                store=f"store_{i}",
                starts_on=date(2026, 8, 1),
                ends_on=date(2026, 8, 14),
                guardrail_config_json="{}",
                created_by="test-user",
            )
            experiments.append(exp)
        return experiments

    def test_list_experiments_default(
        self, repo: ExperimentRepository,
    ) -> None:
        """列出所有实验，按创建时间倒序。"""
        exps = self._create_experiments(repo, 3)
        listed = repo.list_experiments()

        assert len(listed) == 3
        # 按创建时间倒序，最新的在前
        assert listed[0].id == exps[2].id
        assert listed[1].id == exps[1].id
        assert listed[2].id == exps[0].id

    def test_list_experiments_by_platform(
        self, repo: ExperimentRepository,
    ) -> None:
        """按平台筛选实验。"""
        self._create_experiments(repo, 4)
        tiktok_list = repo.list_experiments(platform="tiktok")
        shopee_list = repo.list_experiments(platform="shopee")

        assert len(tiktok_list) == 2
        assert all(e.platform == "tiktok" for e in tiktok_list)
        assert len(shopee_list) == 2
        assert all(e.platform == "shopee" for e in shopee_list)

    def test_list_experiments_by_status(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """按状态筛选实验。"""
        # 创建两个实验，将其中一个更新为 running
        exp1 = repo.create_experiment(**sample_experiment)
        sample_experiment["name"] = "另一个实验"
        exp2 = repo.create_experiment(**sample_experiment)

        repo.update_experiment_status(exp2.id, ExperimentStatus.RUNNING)

        draft_list = repo.list_experiments(status="draft")
        running_list = repo.list_experiments(status="running")

        assert len(draft_list) == 1
        assert draft_list[0].id == exp1.id
        assert len(running_list) == 1
        assert running_list[0].id == exp2.id

    def test_list_experiments_with_limit(
        self, repo: ExperimentRepository,
    ) -> None:
        """限制返回数量。"""
        self._create_experiments(repo, 10)
        limited = repo.list_experiments(limit=3)
        assert len(limited) == 3


# ============================================================================
# 状态转换
# ============================================================================


class TestUpdateExperimentStatus:
    """实验状态转换测试。"""

    def test_update_experiment_status_valid(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """合法状态转换成功。"""
        exp = repo.create_experiment(**sample_experiment)
        assert exp.status == ExperimentStatus.DRAFT

        # draft -> running
        updated = repo.update_experiment_status(exp.id, ExperimentStatus.RUNNING)
        assert updated is not None
        assert updated.status == ExperimentStatus.RUNNING

        # running -> paused
        updated = repo.update_experiment_status(exp.id, ExperimentStatus.PAUSED)
        assert updated is not None
        assert updated.status == ExperimentStatus.PAUSED

        # paused -> running
        updated = repo.update_experiment_status(exp.id, ExperimentStatus.RUNNING)
        assert updated is not None
        assert updated.status == ExperimentStatus.RUNNING

        # running -> completed
        updated = repo.update_experiment_status(exp.id, ExperimentStatus.COMPLETED)
        assert updated is not None
        assert updated.status == ExperimentStatus.COMPLETED

    def test_update_experiment_status_invalid(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """非法状态转换抛出 ValueError。"""
        exp = repo.create_experiment(**sample_experiment)
        # draft -> completed 为非法转换
        with pytest.raises(ValueError, match="Cannot transition from draft to completed"):
            repo.update_experiment_status(exp.id, ExperimentStatus.COMPLETED)

    def test_update_experiment_status_not_found(
        self, repo: ExperimentRepository,
    ) -> None:
        """不存在的实验返回 None。"""
        result = repo.update_experiment_status(
            "non-existent-id", ExperimentStatus.RUNNING
        )
        assert result is None


# ============================================================================
# 变体管理
# ============================================================================


class TestVariants:
    """变体管理测试。"""

    def test_add_variant(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """添加变体返回 ExperimentVariant 对象。"""
        exp = repo.create_experiment(**sample_experiment)

        variant = repo.add_variant(
            experiment_id=exp.id,
            variant_type=VariantType.CONTROL,
            name="对照组-保持原出价",
            strategy_params_json='{"bid_strategy": "target_roas", "roas": 3.0}',
            traffic_percent=50.0,
        )

        assert variant is not None
        assert variant.id is not None
        uuid.UUID(variant.id)
        assert variant.experiment_id == exp.id
        assert variant.variant_type == VariantType.CONTROL
        assert variant.name == "对照组-保持原出价"
        assert variant.traffic_percent == 50.0
        assert variant.status == VariantStatus.ACTIVE
        assert variant.created_at is not None

    def test_get_variants(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """获取实验的所有变体，按创建时间排序。"""
        exp = repo.create_experiment(**sample_experiment)

        # 添加两个变体
        v1 = repo.add_variant(
            experiment_id=exp.id,
            variant_type=VariantType.CONTROL,
            name="对照组",
            strategy_params_json="{}",
            traffic_percent=50.0,
        )
        v2 = repo.add_variant(
            experiment_id=exp.id,
            variant_type=VariantType.TREATMENT,
            name="实验组",
            strategy_params_json="{}",
            traffic_percent=50.0,
        )

        variants = repo.get_variants(exp.id)
        assert len(variants) == 2
        # 按创建时间升序
        assert variants[0].id == v1.id
        assert variants[1].id == v2.id

    def test_get_variants_empty(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """没有变体时返回空列表。"""
        exp = repo.create_experiment(**sample_experiment)
        variants = repo.get_variants(exp.id)
        assert variants == []


# ============================================================================
# 窗口记录
# ============================================================================


class TestWindows:
    """实验窗口数据记录测试。"""

    def test_record_window(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """记录实验窗口数据。"""
        exp = repo.create_experiment(**sample_experiment)
        variant = repo.add_variant(
            experiment_id=exp.id,
            variant_type=VariantType.TREATMENT,
            name="实验组",
            strategy_params_json="{}",
            traffic_percent=50.0,
        )

        window = repo.record_window(
            experiment_id=exp.id,
            data_date="2026-08-01",
            variant_id=variant.id,
            metrics_json=(
                '{"impressions": 10000, "clicks": 500, "spend": "250.00",'
                ' "conversions": 25, "revenue": "750.00"}'
            ),
            guardrail_breaches_json="[]",
        )

        assert window is not None
        assert window.id is not None
        uuid.UUID(window.id)
        assert window.experiment_id == exp.id
        assert window.data_date == "2026-08-01"
        assert window.variant_id == variant.id
        assert window.metrics_json is not None
        assert window.guardrail_breaches_json == "[]"
        assert window.created_at is not None

    def test_record_breach(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """记录护栏违规。"""
        exp = repo.create_experiment(**sample_experiment)
        variant = repo.add_variant(
            experiment_id=exp.id,
            variant_type=VariantType.TREATMENT,
            name="实验组",
            strategy_params_json="{}",
            traffic_percent=50.0,
        )
        window = repo.record_window(
            experiment_id=exp.id,
            data_date="2026-08-01",
            variant_id=variant.id,
            metrics_json='{"spend": "600.00"}',
            guardrail_breaches_json="[]",
        )

        breach = ExperimentGuardrailBreach(
            id=str(uuid.uuid4()),
            experiment_id=exp.id,
            window_id=window.id,
            metric_name="max_daily_spend",
            threshold=500.0,
            actual_value=600.0,
            severity="critical",
            occurred_at=_utc_now(),
            resolved=0,
        )
        repo.record_breach(breach)

        # 验证违规已写入
        breaches = repo.get_breaches(exp.id)
        assert len(breaches) == 1
        assert breaches[0].metric_name == "max_daily_spend"
        assert breaches[0].threshold == 500.0
        assert breaches[0].actual_value == 600.0
        assert breaches[0].severity == "critical"
        assert breaches[0].resolved == 0

    def test_get_breaches(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """获取实验的违规记录，按发生时间倒序。"""
        exp = repo.create_experiment(**sample_experiment)

        # 创建两条违规记录
        breach1 = ExperimentGuardrailBreach(
            id=str(uuid.uuid4()),
            experiment_id=exp.id,
            window_id=None,
            metric_name="max_daily_spend",
            threshold=500.0,
            actual_value=600.0,
            severity="critical",
            occurred_at="2026-08-01T10:00:00",
            resolved=0,
        )
        breach2 = ExperimentGuardrailBreach(
            id=str(uuid.uuid4()),
            experiment_id=exp.id,
            window_id=None,
            metric_name="max_roas_drop_pct",
            threshold=0.3,
            actual_value=0.45,
            severity="warning",
            occurred_at="2026-08-02T10:00:00",
            resolved=1,
        )
        repo.record_breach(breach1)
        repo.record_breach(breach2)

        breaches = repo.get_breaches(exp.id)
        assert len(breaches) == 2
        # 按 occurred_at 倒序
        assert breaches[0].id == breach2.id
        assert breaches[1].id == breach1.id

    def test_get_breaches_empty(
        self, repo: ExperimentRepository, sample_experiment: dict[str, Any]
    ) -> None:
        """没有违规记录时返回空列表。"""
        exp = repo.create_experiment(**sample_experiment)
        breaches = repo.get_breaches(exp.id)
        assert breaches == []
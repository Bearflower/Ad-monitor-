"""adwatch.experiments.service 模块单元测试。

测试 ExperimentService 的实验生命周期、变体管理、护栏评估和查询方法。
使用 tmp_path 创建临时 SQLite 数据库，确保测试隔离性。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from adwatch.domain import DailyAdMetric, Platform
from adwatch.experiments.guardrails import GuardrailEvaluation
from adwatch.experiments.models import (
    Experiment,
    ExperimentGuardrailConfig,
    ExperimentStatus,
    ExperimentVariant,
    VariantStatus,
    VariantType,
)
from adwatch.experiments.service import ExperimentService
from adwatch.storage.db import Database

# ============================================================================
# 测试夹具
# ============================================================================


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """创建临时数据库并执行所有迁移。"""
    database = Database(tmp_path / "test.db")
    database.migrate()
    return database


@pytest.fixture
def service(db: Database) -> ExperimentService:
    """创建 ExperimentService 实例。"""
    return ExperimentService(db)


def _create_experiment(
    service: ExperimentService,
    **overrides: Any,
) -> Experiment:
    """辅助函数：快速创建实验。"""
    params: dict[str, Any] = {
        "name": "测试实验",
        "description": "单元测试实验",
        "platform": "tiktok",
        "store": "store-001",
        "starts_on": date(2026, 1, 1),
        "ends_on": date(2026, 1, 31),
        "created_by": "tester",
        "notes": "",
    }
    params.update(overrides)
    return service.create_experiment(**params)


def _make_metric(
    *,
    platform: Platform = Platform.TIKTOK,
    store: str = "store-001",
    account_id: str = "account-001",
    campaign_id: str = "campaign-001",
    sku_id: str = "sku-001",
    data_date: date | None = None,
    spend: Decimal = Decimal("100.00"),
    gmv: Decimal = Decimal("300.00"),
    orders: int = 10,
) -> DailyAdMetric:
    """辅助函数：快速创建 DailyAdMetric 实例。"""
    return DailyAdMetric(
        platform=platform,
        store=store,
        account_id=account_id,
        campaign_id=campaign_id,
        sku_id=sku_id,
        data_date=data_date or date(2026, 1, 15),
        currency="USD",
        spend=spend,
        attributed_gmv=gmv,
        orders=orders,
        source="test",
    )


def _count_audit_events(db: Database, experiment_id: str) -> int:
    """查询指定实验的审计事件数量。"""
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS cnt FROM audit_events WHERE object_id=?",
            (experiment_id,),
        ).fetchone()
        return row["cnt"] if row else 0


def _fetch_audit_actions(db: Database, experiment_id: str) -> list[str]:
    """查询指定实验的审计事件 action 列表（按创建时间升序）。"""
    with db.transaction() as connection:
        rows = connection.execute(
            "SELECT action FROM audit_events WHERE object_id=? ORDER BY created_at",
            (experiment_id,),
        ).fetchall()
        return [r["action"] for r in rows]


# ============================================================================
# 实验生命周期
# ============================================================================


class TestExperimentLifecycle:
    """实验生命周期管理测试。"""

    def test_create_experiment(self, service: ExperimentService, db: Database) -> None:
        """创建实验，验证返回的 Experiment 对象和审计记录。"""
        # --- 执行 ---
        experiment = _create_experiment(service)

        # --- 验证返回对象 ---
        assert isinstance(experiment, Experiment)
        assert experiment.name == "测试实验"
        assert experiment.platform == "tiktok"
        assert experiment.store == "store-001"
        assert experiment.status == ExperimentStatus.DRAFT
        assert experiment.starts_on == date(2026, 1, 1)
        assert experiment.ends_on == date(2026, 1, 31)
        assert experiment.created_by == "tester"
        assert experiment.id is not None

        # --- 验证持久化 ---
        fetched = service.get_experiment(experiment.id)
        assert fetched is not None
        assert fetched.id == experiment.id

        # --- 验证审计记录 ---
        audit_count = _count_audit_events(db, experiment.id)
        assert audit_count == 1, "创建实验应写入一条审计记录"
        actions = _fetch_audit_actions(db, experiment.id)
        assert actions == ["experiment.created"]

    def test_start_experiment(self, service: ExperimentService, db: Database) -> None:
        """启动实验，状态变为 running，审计记录写入。"""
        experiment = _create_experiment(service)

        # --- 执行 ---
        result = service.start_experiment(experiment.id, actor="tester")

        # --- 验证 ---
        assert result is not None
        assert result.status == ExperimentStatus.RUNNING

        # 验证持久化
        fetched = service.get_experiment(experiment.id)
        assert fetched is not None
        assert fetched.status == ExperimentStatus.RUNNING

        # 验证审计记录：创建 + 启动 = 2 条
        actions = _fetch_audit_actions(db, experiment.id)
        assert actions == ["experiment.created", "experiment.started"]

    def test_pause_experiment(self, service: ExperimentService, db: Database) -> None:
        """暂停实验，状态变为 paused。"""
        experiment = _create_experiment(service)
        service.start_experiment(experiment.id)

        # --- 执行 ---
        result = service.pause_experiment(experiment.id, actor="tester")

        # --- 验证 ---
        assert result is not None
        assert result.status == ExperimentStatus.PAUSED

        fetched = service.get_experiment(experiment.id)
        assert fetched is not None
        assert fetched.status == ExperimentStatus.PAUSED

        actions = _fetch_audit_actions(db, experiment.id)
        assert actions == [
            "experiment.created",
            "experiment.started",
            "experiment.paused",
        ]

    def test_complete_experiment(self, service: ExperimentService, db: Database) -> None:
        """完成实验，状态变为 completed。"""
        experiment = _create_experiment(service)
        service.start_experiment(experiment.id)

        # --- 执行 ---
        result = service.complete_experiment(experiment.id, actor="tester")

        # --- 验证 ---
        assert result is not None
        assert result.status == ExperimentStatus.COMPLETED

        fetched = service.get_experiment(experiment.id)
        assert fetched is not None
        assert fetched.status == ExperimentStatus.COMPLETED

        actions = _fetch_audit_actions(db, experiment.id)
        assert actions == [
            "experiment.created",
            "experiment.started",
            "experiment.completed",
        ]

    def test_cancel_experiment(self, service: ExperimentService, db: Database) -> None:
        """取消实验，状态变为 cancelled。"""
        experiment = _create_experiment(service)

        # --- 执行 ---
        result = service.cancel_experiment(experiment.id, actor="tester")

        # --- 验证 ---
        assert result is not None
        assert result.status == ExperimentStatus.CANCELLED

        fetched = service.get_experiment(experiment.id)
        assert fetched is not None
        assert fetched.status == ExperimentStatus.CANCELLED

        actions = _fetch_audit_actions(db, experiment.id)
        assert actions == [
            "experiment.created",
            "experiment.cancelled",
        ]

    def test_start_nonexistent_experiment(
        self, service: ExperimentService
    ) -> None:
        """启动不存在的实验返回 None。"""
        result = service.start_experiment("nonexistent-id")
        assert result is None

    def test_invalid_transition_raises_error(
        self, service: ExperimentService
    ) -> None:
        """非法状态转换抛出 ValueError。"""
        experiment = _create_experiment(service)
        # draft -> completed 为非法转换
        with pytest.raises(ValueError, match="Cannot transition"):
            service.complete_experiment(experiment.id)


# ============================================================================
# 变体管理
# ============================================================================


class TestVariantManagement:
    """变体管理测试。"""

    def test_add_variant(self, service: ExperimentService, db: Database) -> None:
        """添加变体，验证审计记录。"""
        experiment = _create_experiment(service)

        # --- 执行 ---
        variant = service.add_variant(
            experiment_id=experiment.id,
            variant_type=VariantType.CONTROL,
            name="对照组",
            strategy_params={"budget_pct": 0.5},
            traffic_percent=50.0,
        )

        # --- 验证 ---
        assert isinstance(variant, ExperimentVariant)
        assert variant.experiment_id == experiment.id
        assert variant.variant_type == VariantType.CONTROL
        assert variant.name == "对照组"
        assert variant.traffic_percent == 50.0
        assert variant.status == VariantStatus.ACTIVE

        # 验证审计记录：创建实验 + 添加变体
        actions = _fetch_audit_actions(db, experiment.id)
        assert actions == ["experiment.created", "experiment.variant_added"]

    def test_assign_variant(self, service: ExperimentService, db: Database) -> None:
        """为 store_id 分配变体，验证分配结果。"""
        experiment = _create_experiment(service)
        service.add_variant(
            experiment_id=experiment.id,
            variant_type=VariantType.CONTROL,
            name="对照组",
            traffic_percent=50.0,
        )
        service.add_variant(
            experiment_id=experiment.id,
            variant_type=VariantType.TREATMENT,
            name="实验组",
            traffic_percent=50.0,
        )

        # --- 执行 ---
        result = service.assign_variant(
            store_id="store-001", experiment_id=experiment.id
        )

        # --- 验证 ---
        assert result is not None
        assert result.experiment_id == experiment.id
        assert result.name in ("对照组", "实验组")

        # 同一 store_id 应始终分配到同一变体
        result2 = service.assign_variant(
            store_id="store-001", experiment_id=experiment.id
        )
        assert result2 is not None
        assert result2.id == result.id

        # 验证审计记录包含变体分配
        actions = _fetch_audit_actions(db, experiment.id)
        assert "variant.assigned" in actions

    def test_assign_variant_no_variants(
        self, service: ExperimentService
    ) -> None:
        """没有变体时返回 None。"""
        experiment = _create_experiment(service)

        result = service.assign_variant(
            store_id="store-001", experiment_id=experiment.id
        )
        assert result is None


# ============================================================================
# 护栏评估
# ============================================================================


class TestGuardrailEvaluation:
    """护栏评估测试。"""

    def test_evaluate_experiment_day_passes(
        self, service: ExperimentService, db: Database
    ) -> None:
        """评估某天实验，护栏通过。"""
        guardrail_config = ExperimentGuardrailConfig(
            max_daily_spend=Decimal("1000.00"),
            max_roas_drop_pct=50.0,
        )
        experiment = _create_experiment(
            service, guardrail_config=guardrail_config
        )
        metrics = [
            _make_metric(spend=Decimal("100.00"), gmv=Decimal("500.00")),
        ]

        # --- 执行 ---
        evaluation = service.evaluate_experiment_day(
            experiment_id=experiment.id,
            data_date=date(2026, 1, 15),
            metrics=metrics,
            previous_roas=3.0,
        )

        # --- 验证 ---
        assert isinstance(evaluation, GuardrailEvaluation)
        assert evaluation.passed is True
        assert len(evaluation.breaches) == 0

    def test_evaluate_experiment_day_breaches(
        self, service: ExperimentService, db: Database
    ) -> None:
        """评估某天实验，触发护栏并记录违规。"""
        guardrail_config = ExperimentGuardrailConfig(
            max_daily_spend=Decimal("50.00"),
        )
        experiment = _create_experiment(
            service, guardrail_config=guardrail_config
        )
        metrics = [
            _make_metric(spend=Decimal("100.00"), gmv=Decimal("200.00")),
        ]

        # --- 执行 ---
        evaluation = service.evaluate_experiment_day(
            experiment_id=experiment.id,
            data_date=date(2026, 1, 15),
            metrics=metrics,
        )

        # --- 验证护栏评估结果 ---
        assert evaluation.passed is False
        assert len(evaluation.breaches) >= 1

        # 验证违规已持久化到数据库
        breaches = service.get_breaches(experiment.id)
        assert len(breaches) >= 1
        spent_breach = [b for b in breaches if b.metric_name == "max_daily_spend"]
        assert len(spent_breach) >= 1
        assert spent_breach[0].actual_value == 100.0
        assert spent_breach[0].threshold == 50.0

    def test_evaluate_experiment_day_not_found(
        self, service: ExperimentService
    ) -> None:
        """实验不存在时抛出 ValueError。"""
        with pytest.raises(ValueError, match="Experiment not found"):
            service.evaluate_experiment_day(
                experiment_id="nonexistent-id",
                data_date=date(2026, 1, 15),
                metrics=[],
            )

    def test_evaluate_roas_drop_breach(
        self, service: ExperimentService, db: Database
    ) -> None:
        """ROAS 下降超过阈值时触发护栏。"""
        guardrail_config = ExperimentGuardrailConfig(
            max_roas_drop_pct=10.0,  # 允许下降不超过 10%
        )
        experiment = _create_experiment(
            service, guardrail_config=guardrail_config
        )
        # 当前 ROAS = 200/100 = 2.0，前值 ROAS = 3.0
        # 下降幅度 = (3.0 - 2.0) / 3.0 * 100 = 33.3% > 10%
        metrics = [
            _make_metric(spend=Decimal("100.00"), gmv=Decimal("200.00")),
        ]

        evaluation = service.evaluate_experiment_day(
            experiment_id=experiment.id,
            data_date=date(2026, 1, 15),
            metrics=metrics,
            previous_roas=3.0,
        )

        assert evaluation.passed is False
        roas_breaches = [
            b
            for b in evaluation.breaches
            if b.metric_name == "max_roas_drop_pct"
        ]
        assert len(roas_breaches) >= 1

    def test_evaluate_alert_count_breach(
        self, service: ExperimentService, db: Database
    ) -> None:
        """告警数超过阈值时触发护栏。"""
        guardrail_config = ExperimentGuardrailConfig(
            max_daily_alert_count=5,
        )
        experiment = _create_experiment(
            service, guardrail_config=guardrail_config
        )
        metrics = [
            _make_metric(spend=Decimal("50.00"), gmv=Decimal("150.00")),
        ]

        evaluation = service.evaluate_experiment_day(
            experiment_id=experiment.id,
            data_date=date(2026, 1, 15),
            metrics=metrics,
            alert_count=10,
        )

        assert evaluation.passed is False
        alert_breaches = [
            b
            for b in evaluation.breaches
            if b.metric_name == "max_daily_alert_count"
        ]
        assert len(alert_breaches) >= 1

    def test_evaluate_min_data_days_breach(
        self, service: ExperimentService, db: Database
    ) -> None:
        """连续数据天数不足时触发护栏。"""
        guardrail_config = ExperimentGuardrailConfig(
            min_data_days=7,
        )
        experiment = _create_experiment(
            service, guardrail_config=guardrail_config
        )
        metrics = [
            _make_metric(spend=Decimal("50.00"), gmv=Decimal("150.00")),
        ]

        evaluation = service.evaluate_experiment_day(
            experiment_id=experiment.id,
            data_date=date(2026, 1, 15),
            metrics=metrics,
            consecutive_data_days=3,
        )

        assert evaluation.passed is False
        days_breaches = [
            b
            for b in evaluation.breaches
            if b.metric_name == "min_data_days"
        ]
        assert len(days_breaches) >= 1


# ============================================================================
# 查询方法
# ============================================================================


class TestQueryMethods:
    """查询方法测试。"""

    def test_list_experiments(self, service: ExperimentService) -> None:
        """列出实验。"""
        # 创建多个实验
        _create_experiment(service, name="实验A", platform="tiktok")
        _create_experiment(service, name="实验B", platform="shopee")
        _create_experiment(service, name="实验C", platform="tiktok")

        # --- 全部 ---
        all_exps = service.list_experiments()
        assert len(all_exps) >= 3

        # --- 按平台筛选 ---
        tiktok_exps = service.list_experiments(platform="tiktok")
        assert all(e.platform == "tiktok" for e in tiktok_exps)
        assert len(tiktok_exps) >= 2

        shopee_exps = service.list_experiments(platform="shopee")
        assert all(e.platform == "shopee" for e in shopee_exps)
        assert len(shopee_exps) >= 1

        # --- 按状态筛选 ---
        draft_exps = service.list_experiments(status="draft")
        assert all(e.status == ExperimentStatus.DRAFT for e in draft_exps)

        # --- 限制数量 ---
        limited = service.list_experiments(limit=2)
        assert len(limited) <= 2

    def test_get_experiment(self, service: ExperimentService) -> None:
        """获取单个实验。"""
        experiment = _create_experiment(service)

        # 存在
        fetched = service.get_experiment(experiment.id)
        assert fetched is not None
        assert fetched.id == experiment.id
        assert fetched.name == "测试实验"

        # 不存在
        assert service.get_experiment("nonexistent") is None

    def test_get_variants(self, service: ExperimentService) -> None:
        """获取变体列表。"""
        experiment = _create_experiment(service)

        # 无变体时返回空列表
        assert service.get_variants(experiment.id) == []

        # 添加变体后
        service.add_variant(
            experiment_id=experiment.id,
            variant_type=VariantType.CONTROL,
            name="对照组",
            traffic_percent=50.0,
        )
        service.add_variant(
            experiment_id=experiment.id,
            variant_type=VariantType.TREATMENT,
            name="实验组",
            traffic_percent=50.0,
        )

        variants = service.get_variants(experiment.id)
        assert len(variants) == 2
        names = {v.name for v in variants}
        assert names == {"对照组", "实验组"}

    def test_get_breaches(self, service: ExperimentService, db: Database) -> None:
        """获取违规列表。"""
        experiment = _create_experiment(
            service,
            guardrail_config=ExperimentGuardrailConfig(
                max_daily_spend=Decimal("50.00"),
            ),
        )

        # 无违规时返回空列表
        assert service.get_breaches(experiment.id) == []

        # 触发违规后
        service.evaluate_experiment_day(
            experiment_id=experiment.id,
            data_date=date(2026, 1, 15),
            metrics=[_make_metric(spend=Decimal("100.00"))],
        )

        breaches = service.get_breaches(experiment.id)
        assert len(breaches) >= 1
        assert breaches[0].experiment_id == experiment.id
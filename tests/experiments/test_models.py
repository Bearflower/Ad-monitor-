"""adwatch.experiments.models 模块单元测试。

测试 ExperimentStatus 状态机、choose_variant 分桶分配、
ExperimentGuardrailConfig JSON 序列化/反序列化。
"""

from __future__ import annotations

from decimal import Decimal

from adwatch.experiments.models import (
    ExperimentGuardrailConfig,
    ExperimentStatus,
    ExperimentVariant,
    VariantStatus,
    VariantType,
    choose_variant,
)

# ============================================================================
# ExperimentStatus 状态机
# ============================================================================


class TestExperimentStatusTransitions:
    """ExperimentStatus 状态转换合法性测试。"""

    # --- 合法转换 ---

    def test_draft_can_transition_to_running(self) -> None:
        """draft -> running 合法。"""
        assert ExperimentStatus.DRAFT.can_transition_to(ExperimentStatus.RUNNING)

    def test_draft_can_transition_to_cancelled(self) -> None:
        """draft -> cancelled 合法。"""
        assert ExperimentStatus.DRAFT.can_transition_to(ExperimentStatus.CANCELLED)

    def test_running_can_transition_to_paused(self) -> None:
        """running -> paused 合法。"""
        assert ExperimentStatus.RUNNING.can_transition_to(ExperimentStatus.PAUSED)

    def test_running_can_transition_to_completed(self) -> None:
        """running -> completed 合法。"""
        assert ExperimentStatus.RUNNING.can_transition_to(ExperimentStatus.COMPLETED)

    def test_paused_can_transition_to_running(self) -> None:
        """paused -> running 合法。"""
        assert ExperimentStatus.PAUSED.can_transition_to(ExperimentStatus.RUNNING)

    def test_paused_can_transition_to_cancelled(self) -> None:
        """paused -> cancelled 合法。"""
        assert ExperimentStatus.PAUSED.can_transition_to(ExperimentStatus.CANCELLED)

    # --- 非法转换 ---

    def test_draft_cannot_transition_to_completed(self) -> None:
        """draft -> completed 为非法转换。"""
        assert not ExperimentStatus.DRAFT.can_transition_to(ExperimentStatus.COMPLETED)

    def test_draft_cannot_transition_to_paused(self) -> None:
        """draft -> paused 为非法转换。"""
        assert not ExperimentStatus.DRAFT.can_transition_to(ExperimentStatus.PAUSED)

    def test_running_cannot_transition_to_draft(self) -> None:
        """running -> draft 为非法转换。"""
        assert not ExperimentStatus.RUNNING.can_transition_to(ExperimentStatus.DRAFT)

    def test_running_cannot_transition_to_cancelled(self) -> None:
        """running -> cancelled 为非法转换。"""
        assert not ExperimentStatus.RUNNING.can_transition_to(ExperimentStatus.CANCELLED)

    def test_paused_cannot_transition_to_completed(self) -> None:
        """paused -> completed 为非法转换。"""
        assert not ExperimentStatus.PAUSED.can_transition_to(ExperimentStatus.COMPLETED)

    def test_paused_cannot_transition_to_draft(self) -> None:
        """paused -> draft 为非法转换。"""
        assert not ExperimentStatus.PAUSED.can_transition_to(ExperimentStatus.DRAFT)

    # --- 终态无转换 ---

    def test_completed_has_no_transitions(self) -> None:
        """completed 为终态，不能转换到任何状态。"""
        for status in ExperimentStatus:
            if status == ExperimentStatus.COMPLETED:
                continue
            assert not ExperimentStatus.COMPLETED.can_transition_to(status), (
                f"completed 不应允许转换到 {status.value}"
            )

    def test_cancelled_has_no_transitions(self) -> None:
        """cancelled 为终态，不能转换到任何状态。"""
        for status in ExperimentStatus:
            if status == ExperimentStatus.CANCELLED:
                continue
            assert not ExperimentStatus.CANCELLED.can_transition_to(status), (
                f"cancelled 不应允许转换到 {status.value}"
            )

    def test_reverted_has_no_transitions(self) -> None:
        """reverted 为终态，不能转换到任何状态。"""
        for status in ExperimentStatus:
            if status == ExperimentStatus.REVERTED:
                continue
            assert not ExperimentStatus.REVERTED.can_transition_to(status), (
                f"reverted 不应允许转换到 {status.value}"
            )


# ============================================================================
# choose_variant 分桶分配
# ============================================================================


def _make_variant(
    variant_id: str,
    *,
    traffic_percent: float = 50.0,
    variant_type: VariantType = VariantType.CONTROL,
) -> ExperimentVariant:
    """辅助函数：快速创建 ExperimentVariant 实例。"""
    return ExperimentVariant(
        id=variant_id,
        experiment_id="exp-1",
        variant_type=variant_type,
        name=f"变体-{variant_id}",
        strategy_params_json="{}",
        traffic_percent=traffic_percent,
        status=VariantStatus.ACTIVE,
        created_at="2026-01-01T00:00:00",
    )


class TestChooseVariant:
    """choose_variant 分桶分配逻辑测试。"""

    def test_single_variant(self) -> None:
        """只有一个变体时，始终返回该变体。"""
        variant = _make_variant("v1")
        result = choose_variant("store-001", [variant])
        assert result is not None
        assert result.id == "v1"

    def test_empty_list(self) -> None:
        """空列表返回 None。"""
        assert choose_variant("store-001", []) is None

    def test_same_store_same_variant(self) -> None:
        """同一 store_id 多次调用始终分配到同一变体。"""
        variants = [
            _make_variant("control", traffic_percent=50.0, variant_type=VariantType.CONTROL),
            _make_variant("treatment", traffic_percent=50.0, variant_type=VariantType.TREATMENT),
        ]
        store_id = "store-shopify-001"
        results = [choose_variant(store_id, variants).id for _ in range(10)]
        assert all(r == results[0] for r in results), (
            f"同一 store_id 应始终分配到同一变体，但得到: {results}"
        )

    def test_different_store_different_variant_possible(self) -> None:
        """不同 store_id 可能分配到不同变体（验证确定性哈希不恒等）。"""
        variants = [
            _make_variant("control", traffic_percent=50.0, variant_type=VariantType.CONTROL),
            _make_variant("treatment", traffic_percent=50.0, variant_type=VariantType.TREATMENT),
        ]
        store_ids = [f"store-diff-{i:04d}" for i in range(100)]
        assigned = {sid: choose_variant(sid, variants).id for sid in store_ids}
        unique_assignments = set(assigned.values())
        # 100 个不同 store_id 中至少应出现两种变体
        assert len(unique_assignments) > 1, (
            f"100 个不同 store_id 只分配到 {unique_assignments}，"
            "确定性哈希可能有问题"
        )

    def test_traffic_distribution(self) -> None:
        """验证流量比例分配大致符合 50/50 预期。"""
        variants = [
            _make_variant("control", traffic_percent=50.0, variant_type=VariantType.CONTROL),
            _make_variant("treatment", traffic_percent=50.0, variant_type=VariantType.TREATMENT),
        ]
        # 用足够多的 store_id 来检验分布
        store_ids = [f"store-dist-{i:05d}" for i in range(10000)]
        counts: dict[str, int] = {}
        for sid in store_ids:
            v = choose_variant(sid, variants)
            counts[v.id] = counts.get(v.id, 0) + 1

        total = sum(counts.values())
        for vid, cnt in counts.items():
            ratio = cnt / total
            # 允许 ±5% 的偏差
            assert 0.45 <= ratio <= 0.55, (
                f"变体 {vid} 流量比例为 {ratio:.2%}，"
                f"期望接近 50%，但实际为 {cnt}/{total}"
            )

    def test_traffic_distribution_70_30(self) -> None:
        """验证 70/30 流量比例分配大致正确。"""
        variants = [
            _make_variant("control", traffic_percent=70.0, variant_type=VariantType.CONTROL),
            _make_variant("treatment", traffic_percent=30.0, variant_type=VariantType.TREATMENT),
        ]
        store_ids = [f"store-7030-{i:05d}" for i in range(10000)]
        counts: dict[str, int] = {}
        for sid in store_ids:
            v = choose_variant(sid, variants)
            counts[v.id] = counts.get(v.id, 0) + 1

        total = sum(counts.values())
        for vid, expected_pct in [("control", 0.70), ("treatment", 0.30)]:
            ratio = counts[vid] / total
            # 允许 ±5% 的偏差
            assert 0.65 <= ratio <= 0.75 if vid == "control" else 0.25 <= ratio <= 0.35, (
                f"变体 {vid} 流量比例为 {ratio:.2%}，"
                f"期望接近 {expected_pct:.0%}，但实际为 {counts[vid]}/{total}"
            )

    def test_three_variants_distribution(self) -> None:
        """验证三个变体的流量比例分配。"""
        variants = [
            _make_variant("A", traffic_percent=50.0),
            _make_variant("B", traffic_percent=30.0),
            _make_variant("C", traffic_percent=20.0),
        ]
        store_ids = [f"store-3var-{i:05d}" for i in range(10000)]
        counts: dict[str, int] = {}
        for sid in store_ids:
            v = choose_variant(sid, variants)
            counts[v.id] = counts.get(v.id, 0) + 1

        total = sum(counts.values())
        expectations = [("A", 0.50, 0.45, 0.55), ("B", 0.30, 0.25, 0.35), ("C", 0.20, 0.15, 0.25)]
        for vid, _exp, lo, hi in expectations:
            ratio = counts[vid] / total
            assert lo <= ratio <= hi, (
                f"变体 {vid} 流量比例为 {ratio:.2%}，"
                f"期望在 [{lo:.0%}, {hi:.0%}] 区间，但实际为 {counts[vid]}/{total}"
            )


# ============================================================================
# ExperimentGuardrailConfig JSON 序列化/反序列化
# ============================================================================


class TestExperimentGuardrailConfig:
    """ExperimentGuardrailConfig JSON 序列化/反序列化测试。"""

    def test_from_json(self) -> None:
        """从完整 JSON 解析配置，所有字段正确加载。"""
        raw = (
            '{"max_daily_spend": "500.00", "max_roas_drop_pct": 0.3,'
            ' "max_daily_alert_count": 10, "min_data_days": 3}'
        )
        config = ExperimentGuardrailConfig.from_json(raw)
        assert config.max_daily_spend == Decimal("500.00")
        assert config.max_roas_drop_pct == 0.3
        assert config.max_daily_alert_count == 10
        assert config.min_data_days == 3

    def test_from_json_partial(self) -> None:
        """部分字段的 JSON 解析，未提供字段为 None。"""
        raw = '{"max_daily_spend": "1000.00"}'
        config = ExperimentGuardrailConfig.from_json(raw)
        assert config.max_daily_spend == Decimal("1000.00")
        assert config.max_roas_drop_pct is None
        assert config.max_daily_alert_count is None
        assert config.min_data_days is None

    def test_from_empty_json(self) -> None:
        """空 JSON 或空字符串返回默认配置（所有字段为 None）。"""
        for empty in ("{}", ""):
            config = ExperimentGuardrailConfig.from_json(empty)
            assert config.max_daily_spend is None
            assert config.max_roas_drop_pct is None
            assert config.max_daily_alert_count is None
            assert config.min_data_days is None

    def test_to_json(self) -> None:
        """序列化为 JSON 包含所有非 None 字段。"""
        config = ExperimentGuardrailConfig(
            max_daily_spend=Decimal("500.00"),
            max_roas_drop_pct=0.3,
            max_daily_alert_count=10,
            min_data_days=3,
        )
        import json

        result = json.loads(config.to_json())
        assert result["max_daily_spend"] == "500.00"
        assert result["max_roas_drop_pct"] == 0.3
        assert result["max_daily_alert_count"] == 10
        assert result["min_data_days"] == 3

    def test_to_json_partial(self) -> None:
        """部分字段的配置序列化，只输出非 None 字段。"""
        config = ExperimentGuardrailConfig(
            max_daily_spend=Decimal("1000.00"),
        )
        import json

        result = json.loads(config.to_json())
        assert result == {"max_daily_spend": "1000.00"}

    def test_to_json_empty(self) -> None:
        """所有字段为 None 时序列化为空对象。"""
        config = ExperimentGuardrailConfig()
        assert config.to_json() == "{}"

    def test_round_trip(self) -> None:
        """序列化再反序列化保持值不变。"""
        original = ExperimentGuardrailConfig(
            max_daily_spend=Decimal("888.88"),
            max_roas_drop_pct=0.15,
            max_daily_alert_count=5,
            min_data_days=7,
        )
        json_str = original.to_json()
        restored = ExperimentGuardrailConfig.from_json(json_str)
        assert restored.max_daily_spend == original.max_daily_spend
        assert restored.max_roas_drop_pct == original.max_roas_drop_pct
        assert restored.max_daily_alert_count == original.max_daily_alert_count
        assert restored.min_data_days == original.min_data_days

    def test_round_trip_partial(self) -> None:
        """部分字段的序列化再反序列化保持值不变。"""
        original = ExperimentGuardrailConfig(
            max_daily_spend=Decimal("123.45"),
            min_data_days=10,
        )
        json_str = original.to_json()
        restored = ExperimentGuardrailConfig.from_json(json_str)
        assert restored.max_daily_spend == Decimal("123.45")
        assert restored.min_data_days == 10
        assert restored.max_roas_drop_pct is None
        assert restored.max_daily_alert_count is None

    def test_from_json_max_daily_spend_decimal_precision(self) -> None:
        """max_daily_spend 的 Decimal 精度保持。"""
        raw = '{"max_daily_spend": "123.456789"}'
        config = ExperimentGuardrailConfig.from_json(raw)
        assert config.max_daily_spend == Decimal("123.456789")
        # 往返验证精度
        json_str = config.to_json()
        restored = ExperimentGuardrailConfig.from_json(json_str)
        assert restored.max_daily_spend == Decimal("123.456789")
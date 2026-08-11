"""adwatch.experiments.guardrails 模块单元测试。

测试 evaluate_guardrails 函数的正常场景、违规场景、边界条件，
以及 GuardrailEvaluation 数据类的结构。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric, Platform
from adwatch.experiments.guardrails import (
    GuardrailEvaluation,
    evaluate_guardrails,
)
from adwatch.experiments.models import (
    ExperimentGuardrailBreach,
    ExperimentGuardrailConfig,
    GuardrailSeverity,
)


def _make_metric(
    spend: Decimal = Decimal(0),
    gmv: Decimal = Decimal(0),
    *,
    platform: Platform = Platform.TIKTOK,
    store: str = "test-store",
    data_date: date | None = None,
) -> DailyAdMetric:
    """辅助函数：快速创建 DailyAdMetric 实例。"""
    return DailyAdMetric(
        platform=platform,
        store=store,
        account_id="acc-001",
        campaign_id="camp-001",
        sku_id="sku-001",
        data_date=data_date or date(2026, 8, 1),
        currency="USD",
        spend=spend,
        attributed_gmv=gmv,
        orders=10,
        source="test",
    )


# ============================================================================
# evaluate_guardrails 正常场景
# ============================================================================


class TestNormalScenarios:
    """护栏评估正常通过场景测试。"""

    def test_no_breaches_when_all_within_limits(self) -> None:
        """所有指标都在限制内，护栏通过。"""
        config = ExperimentGuardrailConfig(
            max_daily_spend=Decimal(1000),
            max_roas_drop_pct=30.0,
            max_daily_alert_count=10,
            min_data_days=3,
        )
        metrics = [
            _make_metric(spend=Decimal(500), gmv=Decimal(3000)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            previous_roas=Decimal("5.0"),
            alert_count=5,
            consecutive_data_days=5,
        )

        assert result.passed is True
        assert len(result.breaches) == 0

    def test_no_breaches_with_no_guardrails_configured(self) -> None:
        """没有配置护栏时总是通过。"""
        config = ExperimentGuardrailConfig()
        metrics = [
            _make_metric(spend=Decimal(999999), gmv=Decimal(0)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            previous_roas=Decimal("1.0"),
            alert_count=999,
            consecutive_data_days=0,
        )

        assert result.passed is True
        assert len(result.breaches) == 0


# ============================================================================
# evaluate_guardrails 违规场景
# ============================================================================


class TestBreachScenarios:
    """护栏评估触发违规场景测试。"""

    def test_breach_when_spend_exceeds_max(self) -> None:
        """花费超过 max_daily_spend 触发违规，严重程度为 CRITICAL。"""
        config = ExperimentGuardrailConfig(
            max_daily_spend=Decimal(500),
        )
        metrics = [
            _make_metric(spend=Decimal(600), gmv=Decimal(0)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
        )

        assert result.passed is False
        assert len(result.breaches) == 1

        breach = result.breaches[0]
        assert breach.metric_name == "max_daily_spend"
        assert breach.threshold == 500.0
        assert breach.actual_value == 600.0
        assert breach.severity == GuardrailSeverity.CRITICAL.value
        assert breach.experiment_id == "exp-001"
        assert breach.window_id is None
        assert breach.resolved == 0

    def test_breach_when_roas_drops_significantly(self) -> None:
        """ROAS 下降超过 max_roas_drop_pct 触发违规，严重程度为 WARNING。

        ROAS 从 4.0 下降到 1.0，下降幅度为 75%，超过 30% 阈值。
        """
        config = ExperimentGuardrailConfig(
            max_roas_drop_pct=30.0,
        )
        metrics = [
            _make_metric(spend=Decimal(100), gmv=Decimal(100)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            previous_roas=Decimal("4.0"),
        )

        assert result.passed is False
        assert len(result.breaches) == 1

        breach = result.breaches[0]
        assert breach.metric_name == "max_roas_drop_pct"
        assert breach.threshold == 30.0
        # (4.0 - 1.0) / 4.0 * 100 = 75.0
        assert breach.actual_value == 75.0
        assert breach.severity == GuardrailSeverity.WARNING.value

    def test_breach_when_alert_count_exceeds_max(self) -> None:
        """告警数超过 max_daily_alert_count 触发违规，严重程度为 WARNING。"""
        config = ExperimentGuardrailConfig(
            max_daily_alert_count=5,
        )
        metrics = [
            _make_metric(spend=Decimal(100), gmv=Decimal(100)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            alert_count=10,
        )

        assert result.passed is False
        assert len(result.breaches) == 1

        breach = result.breaches[0]
        assert breach.metric_name == "max_daily_alert_count"
        assert breach.threshold == 5.0
        assert breach.actual_value == 10.0
        assert breach.severity == GuardrailSeverity.WARNING.value

    def test_breach_when_data_days_below_min(self) -> None:
        """连续数据天数低于 min_data_days 触发违规，严重程度为 CRITICAL。"""
        config = ExperimentGuardrailConfig(
            min_data_days=5,
        )
        metrics = [
            _make_metric(spend=Decimal(100), gmv=Decimal(100)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            consecutive_data_days=2,
        )

        assert result.passed is False
        assert len(result.breaches) == 1

        breach = result.breaches[0]
        assert breach.metric_name == "min_data_days"
        assert breach.threshold == 5.0
        assert breach.actual_value == 2.0
        assert breach.severity == GuardrailSeverity.CRITICAL.value

    def test_multiple_breaches(self) -> None:
        """同时触发多个违规，返回所有违规记录。"""
        config = ExperimentGuardrailConfig(
            max_daily_spend=Decimal(500),
            max_roas_drop_pct=30.0,
            max_daily_alert_count=5,
            min_data_days=5,
        )
        metrics = [
            _make_metric(spend=Decimal(600), gmv=Decimal(100)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            previous_roas=Decimal("4.0"),
            alert_count=10,
            consecutive_data_days=2,
        )

        assert result.passed is False
        assert len(result.breaches) == 4

        metric_names = {b.metric_name for b in result.breaches}
        assert metric_names == {
            "max_daily_spend",
            "max_roas_drop_pct",
            "max_daily_alert_count",
            "min_data_days",
        }


# ============================================================================
# evaluate_guardrails 边界条件
# ============================================================================


class TestEdgeCases:
    """护栏评估边界条件测试。"""

    def test_no_roas_breach_when_previous_roas_is_none(self) -> None:
        """没有 previous_roas 时不检查 ROAS 下降。"""
        config = ExperimentGuardrailConfig(
            max_roas_drop_pct=30.0,
        )
        metrics = [
            _make_metric(spend=Decimal(100), gmv=Decimal(10)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            previous_roas=None,
        )

        # 没有 previous_roas，ROAS 检查跳过，其他护栏未配置，应通过
        assert result.passed is True
        assert len(result.breaches) == 0

    def test_no_roas_breach_when_previous_roas_is_zero(self) -> None:
        """previous_roas 为 0 时不检查 ROAS 下降。"""
        config = ExperimentGuardrailConfig(
            max_roas_drop_pct=30.0,
        )
        metrics = [
            _make_metric(spend=Decimal(100), gmv=Decimal(10)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            previous_roas=Decimal(0),
        )

        # previous_roas 为 0，ROAS 检查跳过，应通过
        assert result.passed is True
        assert len(result.breaches) == 0

    def test_no_roas_breach_when_no_metrics_spend(self) -> None:
        """指标花费为 0 时 ROAS 为 None，不触发 ROAS 下降违规。"""
        config = ExperimentGuardrailConfig(
            max_roas_drop_pct=30.0,
        )
        metrics = [
            _make_metric(spend=Decimal(0), gmv=Decimal(0)),
        ]
        result = evaluate_guardrails(
            experiment_id="exp-001",
            guardrail_config=config,
            metrics=metrics,
            data_date=date(2026, 8, 1),
            previous_roas=Decimal("4.0"),
        )

        # 总花费为 0，_calculate_roas 返回 None，ROAS 检查跳过，应通过
        assert result.passed is True
        assert len(result.breaches) == 0


# ============================================================================
# GuardrailEvaluation 结构
# ============================================================================


class TestGuardrailEvaluationStructure:
    """GuardrailEvaluation 数据类结构测试。"""

    def test_guardrail_evaluation_passed(self) -> None:
        """通过的评估 passed=True, breaches 为空。"""
        evaluation = GuardrailEvaluation(
            passed=True,
            breaches=(),
        )

        assert evaluation.passed is True
        assert len(evaluation.breaches) == 0
        assert evaluation.breaches == ()

    def test_guardrail_evaluation_failed(self) -> None:
        """失败的评估 passed=False, breaches 包含记录。"""
        breach = ExperimentGuardrailBreach(
            id="br-001",
            experiment_id="exp-001",
            window_id=None,
            metric_name="max_daily_spend",
            threshold=500.0,
            actual_value=600.0,
            severity=GuardrailSeverity.CRITICAL.value,
            occurred_at="2026-08-01T00:00:00+00:00",
            resolved=0,
        )
        evaluation = GuardrailEvaluation(
            passed=False,
            breaches=(breach,),
        )

        assert evaluation.passed is False
        assert len(evaluation.breaches) == 1
        assert evaluation.breaches[0] is breach
        assert evaluation.breaches[0].metric_name == "max_daily_spend"
        assert evaluation.breaches[0].severity == GuardrailSeverity.CRITICAL.value
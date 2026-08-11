from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from adwatch.domain import DailyAdMetric
from adwatch.experiments.models import (
    ExperimentGuardrailBreach,
    ExperimentGuardrailConfig,
    GuardrailSeverity,
)


@dataclass(frozen=True)
class GuardrailEvaluation:
    passed: bool
    breaches: tuple[ExperimentGuardrailBreach, ...] = ()


def evaluate_guardrails(
    experiment_id: str,
    guardrail_config: ExperimentGuardrailConfig,
    metrics: list[DailyAdMetric],
    data_date: date,
    *,
    previous_roas: Decimal | None = None,
    alert_count: int = 0,
    consecutive_data_days: int = 0,
) -> GuardrailEvaluation:
    """评估实验护栏条件。

    Args:
        experiment_id: 实验 ID
        guardrail_config: 护栏配置
        metrics: 当天的广告指标
        data_date: 数据日期
        previous_roas: 实验前的 ROAS 基准值
        alert_count: 当天告警数
        consecutive_data_days: 连续有数据的天数

    Returns:
        GuardrailEvaluation: 评估结果，包含是否通过和护栏违规列表
    """
    import uuid

    from adwatch.experiments.models import _utc_now

    breaches: list[ExperimentGuardrailBreach] = []
    occurred_at = _utc_now()

    total_spend = sum(m.spend for m in metrics)

    if guardrail_config.max_daily_spend is not None and total_spend > guardrail_config.max_daily_spend:
        breaches.append(
            ExperimentGuardrailBreach(
                id=str(uuid.uuid4()),
                experiment_id=experiment_id,
                window_id=None,
                metric_name="max_daily_spend",
                threshold=float(guardrail_config.max_daily_spend),
                actual_value=float(total_spend),
                severity=GuardrailSeverity.CRITICAL.value,
                occurred_at=occurred_at,
                resolved=0,
            )
        )

    if (
        guardrail_config.max_roas_drop_pct is not None
        and previous_roas is not None
        and previous_roas > 0
    ):
        current_roas = _calculate_roas(metrics)
        if current_roas is not None:
            drop_pct = float(
                (previous_roas - current_roas) / previous_roas * 100
            )
            if drop_pct > guardrail_config.max_roas_drop_pct:
                breaches.append(
                    ExperimentGuardrailBreach(
                        id=str(uuid.uuid4()),
                        experiment_id=experiment_id,
                        window_id=None,
                        metric_name="max_roas_drop_pct",
                        threshold=guardrail_config.max_roas_drop_pct,
                        actual_value=drop_pct,
                        severity=GuardrailSeverity.WARNING.value,
                        occurred_at=occurred_at,
                        resolved=0,
                    )
                )

    if guardrail_config.max_daily_alert_count is not None and alert_count > guardrail_config.max_daily_alert_count:
        breaches.append(
            ExperimentGuardrailBreach(
                id=str(uuid.uuid4()),
                experiment_id=experiment_id,
                window_id=None,
                metric_name="max_daily_alert_count",
                threshold=float(
                    guardrail_config.max_daily_alert_count
                ),
                actual_value=float(alert_count),
                severity=GuardrailSeverity.WARNING.value,
                occurred_at=occurred_at,
                resolved=0,
            )
        )

    if guardrail_config.min_data_days is not None and consecutive_data_days < guardrail_config.min_data_days:
        breaches.append(
            ExperimentGuardrailBreach(
                id=str(uuid.uuid4()),
                experiment_id=experiment_id,
                window_id=None,
                metric_name="min_data_days",
                threshold=float(guardrail_config.min_data_days),
                actual_value=float(consecutive_data_days),
                severity=GuardrailSeverity.CRITICAL.value,
                occurred_at=occurred_at,
                resolved=0,
            )
        )

    return GuardrailEvaluation(
        passed=len(breaches) == 0,
        breaches=tuple(breaches),
    )


def _calculate_roas(metrics: list[DailyAdMetric]) -> Decimal | None:
    total_spend = sum(m.spend for m in metrics)
    total_gmv = sum(m.attributed_gmv for m in metrics)
    if total_spend == 0:
        return None
    return (total_gmv / total_spend).quantize(Decimal("0.0001"))
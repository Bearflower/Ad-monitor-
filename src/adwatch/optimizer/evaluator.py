"""人工反馈归因与可重放的广告调整效果评估。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from adwatch.config import Settings
from adwatch.optimizer.models import (
    Action,
    AdMetric,
    DataQualityStatus,
    Evaluation,
    EvaluationStatus,
    Feedback,
    FeedbackStatus,
    Recommendation,
    WindowStats,
)
from adwatch.optimizer.policy import EffectivenessSummary
from adwatch.optimizer.repository import EvaluationRecord, OptimizerRepository

EVALUATOR_VERSION = "shopee-evaluator/v1"
_EXECUTED_STATUSES = frozenset({FeedbackStatus.EXECUTED, FeedbackStatus.MODIFIED})
_COMPLETED_STATUSES = frozenset(
    {
        EvaluationStatus.EFFECTIVE,
        EvaluationStatus.NO_CHANGE,
        EvaluationStatus.NEGATIVE,
    }
)
_BUDGET_ACTIONS = frozenset(
    {Action.INCREASE_BUDGET, Action.DECREASE_BUDGET}
)
_TARGET_ACTIONS = frozenset(
    {Action.INCREASE_TARGET_ROAS, Action.DECREASE_TARGET_ROAS}
)


class EvaluatorService:
    """只以人工反馈建立归因，不根据页面控制值猜测执行。"""

    def __init__(self, repository: OptimizerRepository, settings: Settings) -> None:
        self.repository = repository
        self.timezone = ZoneInfo(settings.timezone)
        self.effectiveness_min_samples = settings.effectiveness_min_samples

    def record_feedback(
        self,
        store_id: str,
        recommendation_id: str,
        recommendation_revision: int,
        status: FeedbackStatus | str,
        *,
        actual_value: Decimal | None = None,
        executed_at: datetime,
        note: str = "",
    ) -> Feedback:
        """为建议的精确物理修订记录唯一人工反馈。"""
        try:
            parsed_status = (
                status if isinstance(status, FeedbackStatus) else FeedbackStatus(status)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("status is invalid") from error
        recommendation = self.repository.get_recommendation(
            store_id, recommendation_id, recommendation_revision
        )
        if recommendation is None:
            raise ValueError("recommendation revision does not exist for store")
        _require_aware(executed_at, "executed_at")
        if executed_at < recommendation.generated_at:
            raise ValueError("executed_at must not precede recommendation generated_at")
        self._validate_feedback_value(recommendation, parsed_status, actual_value)
        feedback = Feedback(
            feedback_id=_feedback_id(
                store_id, recommendation_id, recommendation_revision
            ),
            recommendation_id=recommendation_id,
            status=parsed_status,
            actual_value=actual_value,
            executed_at=executed_at,
            note=note,
            recommendation_revision=recommendation_revision,
        )
        return self.repository.save_feedback(store_id, feedback)

    def feedback_for(
        self, store_id: str, recommendation_id: str, recommendation_revision: int
    ) -> Feedback | None:
        feedback_id = _feedback_id(
            store_id, recommendation_id, recommendation_revision
        )
        return self.repository.get_feedback(store_id, feedback_id)

    def pending_feedback(self, store_id: str) -> tuple[Feedback, ...]:
        """列出已人工执行、但尚无完成结论的反馈。"""
        return self.repository.pending_feedback_for_store(store_id)

    def observe_campaign_value(
        self,
        *,
        store_id: str,
        campaign_id: str,
        observed_budget: Decimal | None,
        observed_target_roas: Decimal | None,
        observed_at: datetime,
    ) -> None:
        """显式保持只读：页面控制值变化不产生任何反馈或归因。"""
        if not store_id.strip() or not campaign_id.strip():
            raise ValueError("store_id and campaign_id must not be empty")
        _require_aware(observed_at, "observed_at")
        for name, value in (
            ("observed_budget", observed_budget),
            ("observed_target_roas", observed_target_roas),
        ):
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative Decimal")

    def evaluate_feedback(
        self,
        store_id: str,
        feedback_id: str,
        as_of: date,
        evaluated_at: datetime,
    ) -> Evaluation:
        """按冻结窗口评估一条明确执行反馈。"""
        if type(as_of) is not date:
            raise TypeError("as_of must be a date")
        _require_aware(evaluated_at, "evaluated_at")
        if as_of > evaluated_at.astimezone(self.timezone).date():
            raise ValueError("as_of must not be after evaluated_at business date")
        feedback = self.repository.get_feedback(store_id, feedback_id)
        if feedback is None:
            raise ValueError("feedback does not exist for store")
        if feedback.status not in _EXECUTED_STATUSES:
            raise ValueError("only executed or modified feedback can be evaluated")
        if evaluated_at < feedback.executed_at:
            raise ValueError("evaluated_at must not precede executed_at")
        recommendation = self.repository.get_recommendation(
            store_id,
            feedback.recommendation_id,
            feedback.recommendation_revision,
        )
        if recommendation is None:
            raise ValueError("recommendation revision does not exist for store")
        windows = self._windows(feedback, recommendation)
        baseline_rows = self.repository.list_campaign_metrics_between(
            store_id,
            recommendation.campaign_id,
            windows[0],
            windows[1],
            collected_by=evaluated_at,
            data_through=as_of,
        )
        after_rows = self.repository.list_campaign_metrics_between(
            store_id,
            recommendation.campaign_id,
            windows[2],
            windows[3],
            collected_by=evaluated_at,
            data_through=as_of,
        )
        baseline = _aggregate(windows[0], windows[1], baseline_rows)
        after = _aggregate(windows[2], windows[3], after_rows)
        input_dates = tuple(
            sorted({item.data_date for item in baseline_rows + after_rows})
        )
        applied_value = (
            feedback.actual_value
            if feedback.status is FeedbackStatus.MODIFIED
            else recommendation.proposed_value
        )
        if as_of < windows[3]:
            status = EvaluationStatus.PENDING
            note = "观察窗口尚未成熟。"
        elif any(
            item.quality.status is not DataQualityStatus.HEALTHY
            or item.quality.missing_fields
            for item in recommendation.evidence
        ):
            status = EvaluationStatus.INSUFFICIENT_DATA
            note = "建议冻结的数据质量不足，不能可靠归因效果。"
        else:
            missing = _window_issues(windows, baseline_rows, after_rows)
            if missing:
                status = EvaluationStatus.INSUFFICIENT_DATA
                note = "评估窗口缺失或存在重复活动日事实：" + "、".join(missing)
            elif recommendation.action in {Action.CREATE_GROUP, Action.SPLIT_GROUP}:
                status = EvaluationStatus.INSUFFICIENT_DATA
                note = "缺少结构调整后的分组身份事实，不能可靠归因结构动作。"
            elif (
                baseline.spend == 0 or after.spend == 0
            ) and recommendation.success_condition.minimum_roas and recommendation.action is not Action.PAUSE:
                status = EvaluationStatus.INSUFFICIENT_DATA
                note = "观察窗口零花费，ROAS 无定义，不能判定效果。"
            else:
                status, note = _classify(
                    recommendation,
                    baseline,
                    after,
                    tuple(after_rows),
                    applied_value,
                )
                note += _comparison_note(baseline, after)
        input_hash = _evaluation_input_hash(
            feedback,
            recommendation,
            windows,
            baseline_rows,
            after_rows,
            status,
            applied_value,
            as_of,
        )
        existing = tuple(
            item
            for item in self.repository.list_evaluations(
                store_id, recommendation.recommendation_id
            )
            if item.recommendation_revision == recommendation.revision
            and item.feedback_id == feedback.feedback_id
        )
        for item in existing:
            if item.input_hash == input_hash:
                return item
        revision = 1 + max((item.revision for item in existing), default=0)
        evaluation = Evaluation(
            evaluation_id=f"{feedback.feedback_id}:evaluation:{input_hash[:20]}",
            recommendation_id=recommendation.recommendation_id,
            status=status,
            baseline_start=windows[0],
            baseline_end=windows[1],
            observation_start=windows[2],
            observation_end=windows[3],
            algorithm_version=EVALUATOR_VERSION,
            evaluated_at=evaluated_at,
            baseline_stats=baseline,
            after_stats=after,
            note=note,
            recommendation_revision=recommendation.revision,
            feedback_id=feedback.feedback_id,
            applied_value=applied_value,
            input_dates=input_dates,
            input_hash=input_hash,
            revision=revision,
        )
        return self.repository.save_evaluation(store_id, evaluation)

    def effectiveness_summary(
        self,
        store_id: str,
        campaign_id: str,
        action: Action,
        data_date: date,
    ) -> EffectivenessSummary:
        """优先返回活动+动作历史，样本不足时回退到店铺+动作。"""
        if not isinstance(action, Action):
            raise TypeError("action must be an Action")
        if type(data_date) is not date:
            raise TypeError("data_date must be a date")
        exact = self._completed_records(store_id, data_date, campaign_id, action)
        if len(exact) >= self.effectiveness_min_samples:
            return self._summary(exact, action)
        wider = self._completed_records(store_id, data_date, None, action)
        if len(wider) >= self.effectiveness_min_samples:
            return self._summary(wider, action)
        return self._summary(exact, action, allow_step=False)

    def __call__(
        self, store_id: str, campaign_id: str, data_date: date, action: Action
    ) -> EffectivenessSummary:
        """按精确动作返回统计，避免相反方向的历史互相污染。"""
        return self.effectiveness_summary(store_id, campaign_id, action, data_date)

    def _completed_records(
        self,
        store_id: str,
        data_date: date,
        campaign_id: str | None,
        action: Action | None,
    ) -> tuple[EvaluationRecord, ...]:
        evaluated_before = datetime.combine(
            data_date + timedelta(days=1), time.min, self.timezone
        )
        return self.repository.latest_completed_evaluation_records(
            store_id,
            action,
            evaluated_before,
            campaign_id=campaign_id,
        )

    def _summary(
        self,
        records: tuple[EvaluationRecord, ...],
        action: Action,
        *,
        allow_step: bool = True,
    ) -> EffectivenessSummary:
        step = _successful_step(records, frozenset({action})) if allow_step else None
        return EffectivenessSummary(
            sample_size=len(records),
            success_rate=_success_rate(records),
            budget_step=step if action in _BUDGET_ACTIONS else None,
            target_roas_step=step if action in _TARGET_ACTIONS else None,
        )

    def _windows(
        self, feedback: Feedback, recommendation: Recommendation
    ) -> tuple[date, date, date, date]:
        execution_date = feedback.executed_at.astimezone(self.timezone).date()
        days = recommendation.success_condition.observation_days
        baseline_end = execution_date - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=days - 1)
        observation_start = execution_date + timedelta(days=1)
        observation_end = observation_start + timedelta(days=days - 1)
        return baseline_start, baseline_end, observation_start, observation_end

    @staticmethod
    def _validate_feedback_value(
        recommendation: Recommendation,
        status: FeedbackStatus,
        actual_value: Decimal | None,
    ) -> None:
        if status is FeedbackStatus.MODIFIED:
            if recommendation.proposed_value is None:
                raise ValueError("actual_value is unsupported for a non-numeric action")
            if (
                not isinstance(actual_value, Decimal)
                or not actual_value.is_finite()
                or actual_value <= 0
            ):
                raise ValueError("modified feedback requires a positive actual_value")
            assert recommendation.current_value is not None
            if recommendation.action in {
                Action.INCREASE_BUDGET,
                Action.INCREASE_TARGET_ROAS,
            } and actual_value <= recommendation.current_value:
                raise ValueError("actual_value direction must match the recommendation")
            if recommendation.action in {
                Action.DECREASE_BUDGET,
                Action.DECREASE_TARGET_ROAS,
            } and actual_value >= recommendation.current_value:
                raise ValueError("actual_value direction must match the recommendation")
        elif actual_value is not None:
            raise ValueError("actual_value is only allowed for modified feedback")


def _aggregate(
    start: date, end: date, metrics: tuple[AdMetric, ...]
) -> WindowStats:
    return WindowStats(
        start,
        end,
        sum((item.spend for item in metrics), Decimal(0)),
        sum((item.gmv for item in metrics), Decimal(0)),
        sum(item.orders for item in metrics),
    )


def _window_issues(
    windows: tuple[date, date, date, date],
    baseline: tuple[AdMetric, ...],
    after: tuple[AdMetric, ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    for label, start, end, rows in (
        ("基线", windows[0], windows[1], baseline),
        ("观察", windows[2], windows[3], after),
    ):
        counts: dict[date, int] = {}
        for row in rows:
            counts[row.data_date] = counts.get(row.data_date, 0) + 1
        current = start
        while current <= end:
            if counts.get(current, 0) != 1:
                issues.append(f"{label}{current.isoformat()}")
            current += timedelta(days=1)
    return tuple(issues)


def _classify(
    recommendation: Recommendation,
    baseline: WindowStats,
    after: WindowStats,
    after_rows: tuple[AdMetric, ...],
    applied_value: Decimal | None,
) -> tuple[EvaluationStatus, str]:
    floor = recommendation.success_condition.minimum_roas
    rollback_floor = recommendation.rollback_condition.roas_below
    if rollback_floor is not None and _has_rollback_streak(
        after_rows,
        rollback_floor,
        recommendation.rollback_condition.consecutive_days,
    ):
        return EvaluationStatus.NEGATIVE, "观察窗口连续跌破 ROAS 回退线。"
    if recommendation.action is Action.PAUSE and after.spend == 0:
        return EvaluationStatus.EFFECTIVE, "广告花费已停止，止损动作实现。"
    if floor is not None and (after.roas is None or after.roas < floor):
        return EvaluationStatus.NEGATIVE, "观察窗口 ROAS 未守住最低线。"
    threshold = recommendation.success_condition.minimum_gmv_growth
    action_ratio = _applied_change_ratio(recommendation, applied_value)
    if recommendation.action is Action.DECREASE_BUDGET:
        spend_target = baseline.spend * (Decimal(1) - action_ratio)
        gmv_floor = baseline.gmv * (Decimal(1) - threshold)
        if after.spend <= spend_target and after.gmv >= gmv_floor:
            return EvaluationStatus.EFFECTIVE, "花费按执行幅度下降且 GMV 未超容忍跌幅。"
        if after.gmv < gmv_floor:
            return EvaluationStatus.NEGATIVE, "GMV 跌幅超过冻结容忍门槛。"
        return EvaluationStatus.NO_CHANGE, "降耗幅度未达到实际执行动作门槛。"
    if recommendation.action is Action.INCREASE_TARGET_ROAS:
        if baseline.roas is None or after.roas is None:
            return EvaluationStatus.INSUFFICIENT_DATA, "ROAS 无定义，不能评估提效动作。"
        roas_target = baseline.roas * (Decimal(1) + action_ratio)
        gmv_floor = baseline.gmv * (Decimal(1) - threshold)
        if after.roas >= roas_target and after.gmv >= gmv_floor:
            return EvaluationStatus.EFFECTIVE, "ROAS 按执行幅度改善且 GMV 未超容忍跌幅。"
        if after.gmv < gmv_floor:
            return EvaluationStatus.NEGATIVE, "GMV 跌幅超过冻结容忍门槛。"
        return EvaluationStatus.NO_CHANGE, "ROAS 改善未达到实际执行动作门槛。"
    if recommendation.action is Action.PAUSE:
        return EvaluationStatus.NO_CHANGE, "暂停后仍有广告花费，止损动作尚未实现。"
    if recommendation.action is Action.HOLD:
        return EvaluationStatus.INSUFFICIENT_DATA, "保持观察不是可归因的调整动作。"
    if baseline.gmv == 0:
        growth_met = after.gmv > 0
        negative = False
    else:
        growth_met = after.gmv >= baseline.gmv * (Decimal(1) + threshold)
        negative = after.gmv < baseline.gmv * (Decimal(1) - threshold)
    if growth_met:
        return EvaluationStatus.EFFECTIVE, "GMV 达到冻结成功门槛且 ROAS 守线。"
    if negative:
        return EvaluationStatus.NEGATIVE, "GMV 跌幅超过冻结成功门槛。"
    return EvaluationStatus.NO_CHANGE, "GMV 变化未达到冻结成功或负向门槛。"


def _applied_change_ratio(
    recommendation: Recommendation, applied_value: Decimal | None
) -> Decimal:
    if recommendation.current_value is None or applied_value is None:
        return Decimal(0)
    return abs(applied_value / recommendation.current_value - Decimal(1))


def _has_rollback_streak(
    rows: tuple[AdMetric, ...], floor: Decimal, required_days: int
) -> bool:
    streak = 0
    for row in sorted(rows, key=lambda item: item.data_date):
        if row.roas is not None and row.roas < floor:
            streak += 1
            if streak >= required_days:
                return True
        else:
            streak = 0
    return False


def _comparison_note(baseline: WindowStats, after: WindowStats) -> str:
    return (
        f" GMV {baseline.gmv}→{after.gmv}；花费 {baseline.spend}→{after.spend}；"
        f"ROAS {_decimal_text(baseline.roas) or '无定义'}→"
        f"{_decimal_text(after.roas) or '无定义'}；订单 {baseline.orders}→{after.orders}。"
    )


def _feedback_id(store_id: str, recommendation_id: str, revision: int) -> str:
    value = f"{store_id}\0{recommendation_id}\0{revision}".encode()
    return "feedback:" + hashlib.sha256(value).hexdigest()


def _evaluation_input_hash(
    feedback: Feedback,
    recommendation: Recommendation,
    windows: tuple[date, date, date, date],
    baseline: tuple[AdMetric, ...],
    after: tuple[AdMetric, ...],
    status: EvaluationStatus,
    applied_value: Decimal | None,
    as_of: date,
) -> str:
    facts = {
        "algorithm_version": EVALUATOR_VERSION,
        "recommendation": {
            "id": recommendation.recommendation_id,
            "revision": recommendation.revision,
            "action": recommendation.action.value,
            "success": {
                "minimum_roas": _decimal_text(
                    recommendation.success_condition.minimum_roas
                ),
                "minimum_gmv_growth": _decimal_text(
                    recommendation.success_condition.minimum_gmv_growth
                ),
                "observation_days": recommendation.success_condition.observation_days,
            },
            "rollback": {
                "roas_below": _decimal_text(
                    recommendation.rollback_condition.roas_below
                ),
                "consecutive_days": recommendation.rollback_condition.consecutive_days,
            },
        },
        "feedback": {
            "id": feedback.feedback_id,
            "status": feedback.status.value,
            "actual_value": _decimal_text(feedback.actual_value),
            "executed_at": feedback.executed_at.isoformat(),
        },
        "windows": [item.isoformat() for item in windows],
        "as_of": as_of.isoformat(),
        "metrics": [_metric_fact(item) for item in baseline + after],
        "status": status.value,
        "applied_value": _decimal_text(applied_value),
    }
    value = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _metric_fact(metric: AdMetric) -> dict[str, object]:
    return {
        "date": metric.data_date.isoformat(),
        "spend": _decimal_text(metric.spend),
        "gmv": _decimal_text(metric.gmv),
        "orders": metric.orders,
    }


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _success_rate(records: tuple[EvaluationRecord, ...]) -> Decimal | None:
    if not records:
        return None
    successes = sum(
        item.evaluation.status is EvaluationStatus.EFFECTIVE for item in records
    )
    return Decimal(successes) / Decimal(len(records))


def _successful_step(
    records: tuple[EvaluationRecord, ...], actions: frozenset[Action]
) -> Decimal | None:
    values: list[Decimal] = []
    for item in records:
        recommendation = item.recommendation
        evaluation = item.evaluation
        if (
            recommendation.action not in actions
            or evaluation.status is not EvaluationStatus.EFFECTIVE
            or recommendation.current_value is None
            or recommendation.current_value == 0
            or evaluation.applied_value is None
        ):
            continue
        values.append(
            abs(evaluation.applied_value / recommendation.current_value - Decimal(1))
        )
    return None if not values else median(values)


def _require_aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone information")

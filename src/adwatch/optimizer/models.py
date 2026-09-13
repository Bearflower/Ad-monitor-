"""Shopee 广告优化的不可变领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Action(str, Enum):
    INCREASE_BUDGET = "increase_budget"
    DECREASE_BUDGET = "decrease_budget"
    DECREASE_TARGET_ROAS = "decrease_target_roas"
    INCREASE_TARGET_ROAS = "increase_target_roas"
    HOLD = "hold"
    PAUSE = "pause"
    RESUME = "resume"
    CREATE_GROUP = "create_group"
    SPLIT_GROUP = "split_group"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FeedbackStatus(str, Enum):
    EXECUTED = "executed"
    MODIFIED = "modified"
    DEFERRED = "deferred"
    REJECTED = "rejected"


class EvaluationStatus(str, Enum):
    EFFECTIVE = "effective"
    NO_CHANGE = "no_change"
    NEGATIVE = "negative"
    INSUFFICIENT_DATA = "insufficient_data"
    PENDING = "pending"


class DataQualityStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    INVALID = "invalid"


class ProfileStatus(str, Enum):
    TEMPORARY = "temporary"
    FORMAL = "formal"
    NEEDS_FLOOR = "needs_floor"


class CampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"
    UNKNOWN = "unknown"


class MetricGrain(str, Enum):
    OVERVIEW = "overview"
    CAMPAIGN = "campaign"
    ITEM = "item"


class RecommendationSlot(str, Enum):
    PRIMARY = "primary"
    CREATE_GROUP = "create_group"
    SPLIT_GROUP = "split_group"


def _require_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")


def normalize_currency(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("currency must be a string")
    normalized = {"฿": "THB", "$": "USD", "¥": "CNY"}.get(
        value.strip(), value.strip().upper()
    )
    if normalized not in {"THB", "USD", "CNY"}:
        raise ValueError("currency must be THB, USD, or CNY")
    return normalized


def _require_decimal(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _require_non_negative_decimal(value: Decimal, field: str) -> None:
    _require_decimal(value, field)
    if value < 0:
        raise ValueError(f"{field} must not be negative")


def _require_positive_decimal(value: Decimal, field: str) -> None:
    _require_decimal(value, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_non_negative_int(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must not be negative")


def _require_positive_int(value: int, field: str) -> None:
    _require_non_negative_int(value, field)
    if value == 0:
        raise ValueError(f"{field} must be positive")


def _require_date(value: date, field: str) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field} must be a date")


def _require_aware_datetime(value: datetime, field: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone information")


def _require_ratio(
    value: Decimal,
    field: str,
    *,
    minimum: Decimal = Decimal(0),
    maximum: Decimal = Decimal(1),
) -> None:
    _require_decimal(value, field)
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")


@dataclass(frozen=True)
class AdMetric:
    """某店铺、活动、商品在一个本地业务日期的广告事实。"""

    store_id: str
    campaign_id: str
    campaign_name: str
    item_id: str
    data_date: date
    currency: str
    spend: Decimal
    gmv: Decimal
    orders: int
    collected_at: datetime
    source: str
    grain: MetricGrain = MetricGrain.CAMPAIGN

    def __post_init__(self) -> None:
        _require_id(self.store_id, "store_id")
        _require_id(self.campaign_id, "campaign_id")
        _require_id(self.item_id, "item_id")
        _require_date(self.data_date, "data_date")
        object.__setattr__(self, "currency", normalize_currency(self.currency))
        _require_id(self.currency, "currency")
        _require_non_negative_decimal(self.spend, "spend")
        _require_non_negative_decimal(self.gmv, "gmv")
        _require_non_negative_int(self.orders, "orders")
        _require_aware_datetime(self.collected_at, "collected_at")
        _require_id(self.source, "source")
        if not isinstance(self.grain, MetricGrain):
            raise TypeError("grain must be a MetricGrain")

    @property
    def roas(self) -> Decimal | None:
        if self.spend == 0:
            return None
        return (self.gmv / self.spend).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    @property
    def cpa(self) -> Decimal | None:
        if self.orders == 0:
            return None
        return (self.spend / self.orders).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True)
class WindowStats:
    """规则评估窗口中的汇总效果指标。"""

    start_date: date
    end_date: date
    spend: Decimal
    gmv: Decimal
    orders: int

    def __post_init__(self) -> None:
        _require_date(self.start_date, "start_date")
        _require_date(self.end_date, "end_date")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        _require_non_negative_decimal(self.spend, "spend")
        _require_non_negative_decimal(self.gmv, "gmv")
        _require_non_negative_int(self.orders, "orders")

    @property
    def roas(self) -> Decimal | None:
        if self.spend == 0:
            return None
        return (self.gmv / self.spend).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    @property
    def cpa(self) -> Decimal | None:
        if self.orders == 0:
            return None
        return (self.spend / self.orders).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True)
class Evidence:
    """一条可追溯的规则证据及其统计窗口。"""

    code: str
    summary: str
    short_window: WindowStats
    long_window: WindowStats
    quality: DataQuality
    rule_inputs: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_id(self.code, "code")
        _require_id(self.summary, "summary")
        if not isinstance(self.short_window, WindowStats):
            raise TypeError("short_window must be a WindowStats")
        if not isinstance(self.long_window, WindowStats):
            raise TypeError("long_window must be a WindowStats")
        if not isinstance(self.quality, DataQuality):
            raise TypeError("quality must be a DataQuality")
        if not isinstance(self.rule_inputs, tuple):
            raise TypeError("rule_inputs must be a tuple")
        for item in self.rule_inputs:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(value, str) and value.strip() for value in item)
            ):
                raise ValueError("rule_inputs must contain non-empty key-value tuples")


@dataclass(frozen=True)
class SuccessCondition:
    """建议被判定成功所需达到的指标门槛。"""

    minimum_roas: Decimal | None
    minimum_gmv_growth: Decimal
    observation_days: int

    def __post_init__(self) -> None:
        if self.minimum_roas is not None:
            _require_positive_decimal(self.minimum_roas, "minimum_roas")
        _require_ratio(self.minimum_gmv_growth, "minimum_gmv_growth")
        _require_positive_int(self.observation_days, "observation_days")


@dataclass(frozen=True)
class RollbackCondition:
    """建议需要回退时的指标门槛。"""

    roas_below: Decimal | None
    consecutive_days: int

    def __post_init__(self) -> None:
        if self.roas_below is not None:
            _require_non_negative_decimal(self.roas_below, "roas_below")
        _require_positive_int(self.consecutive_days, "consecutive_days")


@dataclass(frozen=True)
class DataQuality:
    status: DataQualityStatus
    assessed_at: datetime
    allow_aggressive_scale: bool = False
    missing_fields: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, DataQualityStatus):
            raise TypeError("status must be a DataQualityStatus")
        _require_aware_datetime(self.assessed_at, "assessed_at")
        if not isinstance(self.allow_aggressive_scale, bool):
            raise TypeError("allow_aggressive_scale must be a bool")
        if not isinstance(self.missing_fields, tuple):
            raise TypeError("missing_fields must be a tuple")
        if any(
            not isinstance(field, str) or not field.strip()
            for field in self.missing_fields
        ):
            raise ValueError("missing_fields must contain non-empty field names")
        if self.allow_aggressive_scale and (
            self.status is not DataQualityStatus.HEALTHY or self.missing_fields
        ):
            raise ValueError(
                "allow_aggressive_scale requires healthy data without missing fields"
            )


@dataclass(frozen=True)
class CampaignProfile:
    store_id: str
    campaign_id: str
    status: ProfileStatus
    effective_from: date
    minimum_roas: Decimal | None
    minimum_gmv_growth: Decimal
    growth_intensity: Decimal
    max_budget_step: Decimal
    max_target_roas_step: Decimal
    min_orders: int
    cooldown_days: int
    evaluation_days: int
    rollback_days: int
    allowed_actions: frozenset[Action]
    note: str = ""

    def __post_init__(self) -> None:
        _require_id(self.store_id, "store_id")
        _require_id(self.campaign_id, "campaign_id")
        if not isinstance(self.status, ProfileStatus):
            raise TypeError("status must be a ProfileStatus")
        _require_date(self.effective_from, "effective_from")
        if self.minimum_roas is not None:
            _require_positive_decimal(self.minimum_roas, "minimum_roas")
        _require_ratio(self.minimum_gmv_growth, "minimum_gmv_growth")
        _require_ratio(self.growth_intensity, "growth_intensity")
        _require_ratio(self.max_budget_step, "max_budget_step")
        _require_ratio(self.max_target_roas_step, "max_target_roas_step")
        _require_positive_int(self.min_orders, "min_orders")
        _require_non_negative_int(self.cooldown_days, "cooldown_days")
        _require_positive_int(self.evaluation_days, "evaluation_days")
        _require_positive_int(self.rollback_days, "rollback_days")
        if not isinstance(self.allowed_actions, frozenset):
            raise TypeError("allowed_actions must be a frozenset")
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        if not all(isinstance(action, Action) for action in self.allowed_actions):
            raise TypeError("allowed_actions must contain Action values")


@dataclass(frozen=True)
class CampaignSnapshot:
    """广告活动控制面快照，不耦合每日指标或数据质量。"""

    store_id: str
    campaign_id: str
    campaign_name: str
    campaign_type: str
    status: CampaignStatus
    current_budget: Decimal | None
    target_roas: Decimal | None
    currency: str
    observed_at: datetime
    data_date: date

    def __post_init__(self) -> None:
        _require_id(self.store_id, "store_id")
        _require_id(self.campaign_id, "campaign_id")
        _require_id(self.campaign_type, "campaign_type")
        if not isinstance(self.status, CampaignStatus):
            raise TypeError("status must be a CampaignStatus")
        if self.current_budget is not None:
            _require_non_negative_decimal(self.current_budget, "current_budget")
        if self.target_roas is not None:
            _require_positive_decimal(self.target_roas, "target_roas")
        object.__setattr__(self, "currency", normalize_currency(self.currency))
        _require_id(self.currency, "currency")
        _require_aware_datetime(self.observed_at, "observed_at")
        _require_date(self.data_date, "data_date")


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    store_id: str
    campaign_id: str
    data_date: date
    action: Action
    confidence: Confidence
    priority: int
    current_value: Decimal | None
    proposed_value: Decimal | None
    change_ratio: Decimal | None
    evidence: tuple[Evidence, ...]
    success_condition: SuccessCondition
    rollback_condition: RollbackCondition
    expected_impact: Decimal
    risk: Decimal
    observation_days: int
    rule_version: str
    input_hash: str
    revision: int
    generated_at: datetime
    slot: RecommendationSlot = RecommendationSlot.PRIMARY

    def __post_init__(self) -> None:
        _require_id(self.recommendation_id, "recommendation_id")
        _require_id(self.store_id, "store_id")
        _require_id(self.campaign_id, "campaign_id")
        _require_date(self.data_date, "data_date")
        if not isinstance(self.action, Action):
            raise TypeError("action must be an Action")
        if not isinstance(self.slot, RecommendationSlot):
            raise TypeError("slot must be a RecommendationSlot")
        if not isinstance(self.confidence, Confidence):
            raise TypeError("confidence must be a Confidence")
        _require_positive_int(self.priority, "priority")
        if self.priority > 5:
            raise ValueError("priority must be between 1 and 5")
        for field, value in (
            ("current_value", self.current_value),
            ("proposed_value", self.proposed_value),
        ):
            if value is not None:
                _require_non_negative_decimal(value, field)
        if self.change_ratio is not None:
            _require_ratio(
                self.change_ratio,
                "change_ratio",
                minimum=Decimal(-1),
            )
        if not isinstance(self.evidence, tuple):
            raise TypeError("evidence must be a tuple")
        if not self.evidence:
            raise ValueError("evidence must not be empty")
        if not all(isinstance(item, Evidence) for item in self.evidence):
            raise TypeError("evidence must contain Evidence values")
        if not isinstance(self.success_condition, SuccessCondition):
            raise TypeError("success_condition must be a SuccessCondition")
        if not isinstance(self.rollback_condition, RollbackCondition):
            raise TypeError("rollback_condition must be a RollbackCondition")
        _require_ratio(self.expected_impact, "expected_impact")
        _require_ratio(self.risk, "risk")
        _require_positive_int(self.observation_days, "observation_days")
        _require_id(self.rule_version, "rule_version")
        _require_id(self.input_hash, "input_hash")
        _require_positive_int(self.revision, "revision")
        _require_aware_datetime(self.generated_at, "generated_at")
        self._validate_numeric_action()

    def _validate_numeric_action(self) -> None:
        increase_actions = {
            Action.INCREASE_BUDGET,
            Action.INCREASE_TARGET_ROAS,
        }
        decrease_actions = {
            Action.DECREASE_BUDGET,
            Action.DECREASE_TARGET_ROAS,
        }
        numeric_actions = increase_actions | decrease_actions
        if self.action not in numeric_actions:
            if any(
                value is not None
                for value in (
                    self.current_value,
                    self.proposed_value,
                    self.change_ratio,
                )
            ):
                raise ValueError(f"{self.action.value} actions require null values")
            return
        if None in (
            self.current_value,
            self.proposed_value,
            self.change_ratio,
        ):
            raise ValueError("numeric actions require values and change_ratio")
        if self.current_value == 0:
            raise ValueError("current_value must be positive for numeric actions")
        if (
            self.action in increase_actions
            and self.proposed_value <= self.current_value
        ):
            raise ValueError("proposed_value must increase for increase actions")
        if (
            self.action in decrease_actions
            and self.proposed_value >= self.current_value
        ):
            raise ValueError("proposed_value must decrease for decrease actions")
        proposed_quantum = Decimal(1).scaleb(self.proposed_value.as_tuple().exponent)
        expected_value = (
            self.current_value * (Decimal(1) + self.change_ratio)
        ).quantize(proposed_quantum, rounding=ROUND_HALF_UP)
        if self.proposed_value != expected_value:
            raise ValueError("change_ratio must match the proposed value change")


@dataclass(frozen=True)
class Feedback:
    feedback_id: str
    recommendation_id: str
    status: FeedbackStatus
    actual_value: Decimal | None
    executed_at: datetime
    note: str = ""
    recommendation_revision: int = 1

    def __post_init__(self) -> None:
        _require_id(self.feedback_id, "feedback_id")
        _require_id(self.recommendation_id, "recommendation_id")
        if not isinstance(self.status, FeedbackStatus):
            raise TypeError("status must be a FeedbackStatus")
        if self.actual_value is not None:
            _require_non_negative_decimal(self.actual_value, "actual_value")
        if self.status is FeedbackStatus.MODIFIED and self.actual_value is None:
            raise ValueError("modified feedback requires actual_value")
        if self.status is not FeedbackStatus.MODIFIED and self.actual_value is not None:
            raise ValueError("actual_value is only allowed for modified feedback")
        _require_aware_datetime(self.executed_at, "executed_at")
        _require_positive_int(self.recommendation_revision, "recommendation_revision")


@dataclass(frozen=True)
class Evaluation:
    evaluation_id: str
    recommendation_id: str
    status: EvaluationStatus
    baseline_start: date
    baseline_end: date
    observation_start: date
    observation_end: date
    algorithm_version: str
    evaluated_at: datetime
    baseline_stats: WindowStats
    after_stats: WindowStats
    note: str = ""
    recommendation_revision: int = 1
    feedback_id: str = "legacy-feedback"
    applied_value: Decimal | None = None
    input_dates: tuple[date, ...] = ()
    input_hash: str = "legacy-input"
    revision: int = 1

    def __post_init__(self) -> None:
        _require_id(self.evaluation_id, "evaluation_id")
        _require_id(self.recommendation_id, "recommendation_id")
        if not isinstance(self.status, EvaluationStatus):
            raise TypeError("status must be an EvaluationStatus")
        for field, value in (
            ("baseline_start", self.baseline_start),
            ("baseline_end", self.baseline_end),
            ("observation_start", self.observation_start),
            ("observation_end", self.observation_end),
        ):
            _require_date(value, field)
        if self.baseline_start > self.baseline_end:
            raise ValueError("baseline_start must not be after baseline_end")
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        if self.baseline_end >= self.observation_start:
            raise ValueError("observation_start must follow baseline_end")
        _require_id(self.algorithm_version, "algorithm_version")
        _require_aware_datetime(self.evaluated_at, "evaluated_at")
        if not isinstance(self.baseline_stats, WindowStats):
            raise TypeError("baseline_stats must be a WindowStats")
        if not isinstance(self.after_stats, WindowStats):
            raise TypeError("after_stats must be a WindowStats")
        if (
            self.baseline_stats.start_date,
            self.baseline_stats.end_date,
        ) != (self.baseline_start, self.baseline_end):
            raise ValueError("baseline_stats must match the baseline window")
        if (self.after_stats.start_date, self.after_stats.end_date) != (
            self.observation_start,
            self.observation_end,
        ):
            raise ValueError("after_stats must match the observation window")
        _require_positive_int(self.recommendation_revision, "recommendation_revision")
        _require_id(self.feedback_id, "feedback_id")
        if self.applied_value is not None:
            _require_non_negative_decimal(self.applied_value, "applied_value")
        if not isinstance(self.input_dates, tuple):
            raise TypeError("input_dates must be a tuple")
        if any(type(value) is not date for value in self.input_dates):
            raise TypeError("input_dates must contain dates")
        if len(set(self.input_dates)) != len(self.input_dates):
            raise ValueError("input_dates must not contain duplicates")
        _require_id(self.input_hash, "input_hash")
        _require_positive_int(self.revision, "revision")

"""只读、可回放的 Shopee 活动优化策略。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from adwatch.config import Settings
from adwatch.optimizer.models import (
    Action,
    AdMetric,
    CampaignProfile,
    CampaignSnapshot,
    CampaignStatus,
    Confidence,
    DataQuality,
    DataQualityStatus,
    Evidence,
    MetricGrain,
    ProfileStatus,
    Recommendation,
    RecommendationSlot,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)
from adwatch.optimizer.repository import OptimizerRepository

POLICY_VERSION = "shopee-policy/v1"
_NUMERIC_ACTIONS = frozenset(
    {
        Action.INCREASE_BUDGET,
        Action.DECREASE_BUDGET,
        Action.DECREASE_TARGET_ROAS,
        Action.INCREASE_TARGET_ROAS,
    }
)
_ACTION_PRIORITY = {
    Action.PAUSE: 1,
    Action.INCREASE_BUDGET: 2,
    Action.DECREASE_BUDGET: 2,
    Action.DECREASE_TARGET_ROAS: 2,
    Action.INCREASE_TARGET_ROAS: 2,
    Action.RESUME: 2,
    Action.CREATE_GROUP: 3,
    Action.SPLIT_GROUP: 3,
    Action.HOLD: 4,
}


@dataclass(frozen=True)
class PolicyTemplate:
    """由设置构成且在每条建议证据中冻结的统一策略模板。"""

    rule_version: str
    min_history_days: int
    min_budget_utilization: Decimal
    floor_guard_band: Decimal
    severe_underperformance_ratio: Decimal
    structure_winner_share: Decimal
    structure_min_items: int
    low_confidence_budget_step: Decimal
    default_budget_step: Decimal
    global_max_budget_step: Decimal
    global_max_target_roas_step: Decimal
    effectiveness_min_samples: int
    min_orders: int
    cooldown_days: int
    evaluation_days: int
    rollback_days: int
    default_growth_intensity: Decimal
    minimum_gmv_growth: Decimal
    short_window_days: int
    long_window_days: int
    budget_quantum: Decimal
    roas_quantum: Decimal

    @classmethod
    def from_settings(cls, settings: Settings) -> PolicyTemplate:
        return cls(
            rule_version=POLICY_VERSION,
            min_history_days=settings.min_history_days,
            min_budget_utilization=settings.min_budget_utilization,
            floor_guard_band=settings.floor_guard_band,
            severe_underperformance_ratio=settings.severe_underperformance_ratio,
            structure_winner_share=settings.structure_winner_share,
            structure_min_items=settings.structure_min_items,
            low_confidence_budget_step=settings.low_confidence_budget_step,
            default_budget_step=settings.default_budget_step,
            global_max_budget_step=settings.max_budget_step,
            global_max_target_roas_step=settings.max_target_roas_step,
            effectiveness_min_samples=settings.effectiveness_min_samples,
            min_orders=settings.min_orders,
            cooldown_days=settings.cooldown_days,
            evaluation_days=settings.evaluation_days,
            rollback_days=settings.rollback_days,
            default_growth_intensity=settings.default_growth_intensity,
            minimum_gmv_growth=settings.minimum_gmv_growth,
            short_window_days=settings.short_window_days,
            long_window_days=settings.long_window_days,
            budget_quantum=settings.budget_quantum,
            roas_quantum=settings.roas_quantum,
        )


@dataclass(frozen=True)
class EffectivenessSummary:
    """供后续效果评估模块注入的、非强制步长校准结果。"""

    sample_size: int
    success_rate: Decimal | None = None
    budget_step: Decimal | None = None
    target_roas_step: Decimal | None = None

    def __post_init__(self) -> None:
        if isinstance(self.sample_size, bool) or not isinstance(self.sample_size, int):
            raise TypeError("sample_size must be an integer")
        if self.sample_size < 0:
            raise ValueError("sample_size must not be negative")
        for field in ("success_rate", "budget_step", "target_roas_step"):
            value = getattr(self, field)
            if value is None:
                continue
            if not isinstance(value, Decimal):
                raise TypeError(f"{field} must be a Decimal")
            if not value.is_finite() or value < 0:
                raise ValueError(f"{field} must be finite and non-negative")


class EffectivenessProvider(Protocol):
    def __call__(
        self, store_id: str, campaign_id: str, data_date: date, action: Action
    ) -> EffectivenessSummary | None: ...


class Optimizer:
    """仅生成并保存建议，绝不触发任何广告平台写操作。"""

    def __init__(
        self,
        repository: OptimizerRepository,
        settings: Settings,
        *,
        effectiveness_provider: EffectivenessProvider | None = None,
    ) -> None:
        self.repository = repository
        self.template = PolicyTemplate.from_settings(settings)
        self.effectiveness_provider = effectiveness_provider

    def recommend(
        self,
        store_id: str,
        campaign_id: str,
        data_date: date,
        quality: DataQuality,
        decision_at: datetime,
    ) -> tuple[Recommendation, ...]:
        """基于同一活动的 campaign 粒度事实输出可解释建议。"""
        return self.repository.save_recommendations(
            self.plan(store_id, campaign_id, data_date, quality, decision_at)
        )

    def plan(
        self,
        store_id: str,
        campaign_id: str,
        data_date: date,
        quality: DataQuality,
        decision_at: datetime,
    ) -> tuple[Recommendation, ...]:
        """计算同一活动的完整建议集合，不写入建议仓储。"""
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        snapshot = self.repository.latest_snapshot_for(
            store_id, campaign_id, data_date, decision_at=decision_at
        )
        profile, profile_version = self._profile(
            store_id, campaign_id, data_date, snapshot
        )
        short = self._window(
            store_id, campaign_id, data_date, self._short_days(profile)
        )
        long = self._window(store_id, campaign_id, data_date, self._long_days(profile))
        history_days = self._history_days(
            store_id, campaign_id, data_date, self._long_days(profile)
        )
        historical_insufficient = (
            len(history_days) < self.template.min_history_days
            or long.orders < profile.min_orders
        )
        recommendation = self._parameter_recommendation(
            store_id=store_id,
            campaign_id=campaign_id,
            data_date=data_date,
            quality=quality,
            profile=profile,
            profile_version=profile_version,
            snapshot=snapshot,
            short=short,
            long=long,
            historical_insufficient=historical_insufficient,
            observed_days=len(history_days),
            decision_at=decision_at,
        )
        return (
            recommendation,
            *self._structure_recommendations(
                store_id=store_id,
                campaign_id=campaign_id,
                data_date=data_date,
                quality=quality,
                profile=profile,
                profile_version=profile_version,
                snapshot=snapshot,
                short=short,
                long=long,
                historical_insufficient=historical_insufficient,
                observed_days=len(history_days),
                decision_at=decision_at,
            ),
        )

    def ensure_profile(
        self,
        store_id: str,
        campaign_id: str,
        data_date: date,
        decision_at: datetime,
    ) -> CampaignProfile:
        """仅建立或刷新临时档案，不生成建议。"""
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        snapshot = self.repository.latest_snapshot_for(
            store_id, campaign_id, data_date, decision_at=decision_at
        )
        profile, _ = self._profile(store_id, campaign_id, data_date, snapshot)
        return profile

    def _profile(
        self,
        store_id: str,
        campaign_id: str,
        data_date: date,
        snapshot: CampaignSnapshot | None,
    ) -> tuple[CampaignProfile, int]:
        current = self.repository.get_profile_as_of(store_id, campaign_id, data_date)
        target = None if snapshot is None else snapshot.target_roas
        if current is None:
            current = self._new_profile(store_id, campaign_id, data_date, target)
            self.repository.save_profile(current)
        elif (
            current.status in {ProfileStatus.TEMPORARY, ProfileStatus.NEEDS_FLOOR}
            and target != current.minimum_roas
        ):
            current = replace(
                current,
                minimum_roas=target,
                effective_from=data_date,
                status=(
                    ProfileStatus.TEMPORARY
                    if target is not None
                    else ProfileStatus.NEEDS_FLOOR
                ),
            )
            self.repository.save_profile(current)
        selected = self.repository.get_profile_as_of(store_id, campaign_id, data_date)
        version = self.repository.get_profile_version_as_of(
            store_id, campaign_id, data_date
        )
        assert selected is not None and version is not None
        return selected, version

    def _new_profile(
        self, store_id: str, campaign_id: str, data_date: date, target: Decimal | None
    ) -> CampaignProfile:
        return CampaignProfile(
            store_id=store_id,
            campaign_id=campaign_id,
            status=(
                ProfileStatus.TEMPORARY
                if target is not None
                else ProfileStatus.NEEDS_FLOOR
            ),
            effective_from=data_date,
            minimum_roas=target,
            minimum_gmv_growth=self.template.minimum_gmv_growth,
            growth_intensity=self.template.default_growth_intensity,
            max_budget_step=self.template.global_max_budget_step,
            max_target_roas_step=self.template.global_max_target_roas_step,
            min_orders=self.template.min_orders,
            cooldown_days=self.template.cooldown_days,
            evaluation_days=self.template.evaluation_days,
            rollback_days=self.template.rollback_days,
            allowed_actions=frozenset(Action),
            note="由首次广告控制面快照自动初始化；正式档案可覆盖。",
        )

    def _parameter_recommendation(
        self,
        *,
        store_id: str,
        campaign_id: str,
        data_date: date,
        quality: DataQuality,
        profile: CampaignProfile,
        profile_version: int,
        snapshot: CampaignSnapshot | None,
        short: WindowStats,
        long: WindowStats,
        historical_insufficient: bool,
        observed_days: int,
        decision_at: datetime,
    ) -> Recommendation:
        action = Action.HOLD
        reason = "维持观察；不满足安全调整条件。"
        numeric: tuple[Decimal, Decimal, Decimal] | None = None
        step_inputs: tuple[tuple[str, str], ...] = ()
        confidence = Confidence.LOW if historical_insufficient else Confidence.HIGH
        if not self._quality_is_complete(quality):
            reason = "数据质量无效、过期或不完整，禁止数值扩量。"
            confidence = Confidence.LOW
        elif snapshot is None:
            reason = "缺少决策时点可用的活动控制面快照，安全维持观察。"
            confidence = Confidence.LOW
        elif (
            profile.status is ProfileStatus.NEEDS_FLOOR or profile.minimum_roas is None
        ):
            reason = "尚未配置最低 ROAS，需人工补充底线。"
            confidence = Confidence.LOW
        elif self._in_cooldown(store_id, campaign_id, data_date, profile.cooldown_days):
            reason = "仍在冷静期，等待既有建议的观察窗口结束。"
            confidence = Confidence.LOW
        elif snapshot is not None and snapshot.status is CampaignStatus.PAUSED:
            if not historical_insufficient and self._at_or_above_floor(long, profile):
                action, reason = Action.RESUME, "暂停活动的后续历史已恢复到最低 ROAS。"
            else:
                reason = "活动已暂停，尚无足够且达标的恢复历史。"
        elif snapshot is not None and snapshot.status is not CampaignStatus.ACTIVE:
            reason = "活动已结束或状态未知，安全维持观察。"
            confidence = Confidence.LOW
        elif long.roas is None:
            reason = "窗口内无广告消耗，无法计算 ROAS。"
            confidence = Confidence.LOW
        else:
            action, reason, numeric, step_inputs = self._evaluate_active(
                profile,
                snapshot,
                short,
                long,
                historical_insufficient,
                observed_days,
                data_date,
            )
        if action is not Action.HOLD and action not in profile.allowed_actions:
            action, numeric = Action.HOLD, None
            reason = "该动作未获活动档案授权，安全维持观察。"
        if action in _NUMERIC_ACTIONS and numeric is None:
            action = Action.HOLD
            reason = "缺少当前预算或目标 ROAS，不能生成对应数值建议。"
        return self._recommendation(
            store_id=store_id,
            campaign_id=campaign_id,
            data_date=data_date,
            action=action,
            confidence=confidence,
            short=short,
            long=long,
            quality=quality,
            profile=profile,
            profile_version=profile_version,
            snapshot=snapshot,
            reason=reason,
            numeric=numeric,
            extra_inputs=(("recommendation_kind", "parameter"),) + step_inputs,
            decision_at=decision_at,
        )

    def _evaluate_active(
        self,
        profile: CampaignProfile,
        snapshot: CampaignSnapshot | None,
        short: WindowStats,
        long: WindowStats,
        historical_insufficient: bool,
        observed_days: int,
        data_date: date,
    ) -> tuple[
        Action,
        str,
        tuple[Decimal, Decimal, Decimal] | None,
        tuple[tuple[str, str], ...],
    ]:
        assert profile.minimum_roas is not None
        assert long.roas is not None
        roas = long.roas
        floor = profile.minimum_roas
        if roas >= floor:
            if self._budget_saturated(snapshot, long, observed_days):
                budget_step, step_inputs = self._step(
                    profile, Action.INCREASE_BUDGET, historical_insufficient, data_date
                )
                return (
                    Action.INCREASE_BUDGET,
                    "窗口 ROAS 达标且预算利用率达到策略门槛。",
                    self._numeric(
                        snapshot.current_budget if snapshot else None,
                        budget_step,
                        self.template.budget_quantum,
                    ),
                    step_inputs,
                )
            target_step, step_inputs = self._step(
                profile, Action.DECREASE_TARGET_ROAS, historical_insufficient, data_date
            )
            return (
                Action.DECREASE_TARGET_ROAS,
                "窗口 ROAS 达标但预算未充分使用，保守降低目标以获取流量。",
                self._numeric(
                    snapshot.target_roas if snapshot else None,
                    -target_step,
                    self.template.roas_quantum,
                ),
                step_inputs,
            )
        guard_floor = floor * (Decimal(1) - self.template.floor_guard_band)
        if roas >= guard_floor:
            return Action.HOLD, "窗口 ROAS 接近最低线，保持观察。", None, ()
        severe_floor = floor * self.template.severe_underperformance_ratio
        if (
            not historical_insufficient
            and short.roas is not None
            and roas <= severe_floor
            and short.roas <= severe_floor
        ):
            return Action.PAUSE, "窗口 ROAS 持续严重低于最低线，建议暂停。", None, ()
        if snapshot is not None and snapshot.current_budget is not None:
            budget_step, step_inputs = self._step(
                profile, Action.DECREASE_BUDGET, historical_insufficient, data_date
            )
            return (
                Action.DECREASE_BUDGET,
                "窗口 ROAS 低于最低线，建议保守降低预算。",
                self._numeric(
                    snapshot.current_budget, -budget_step, self.template.budget_quantum
                ),
                step_inputs,
            )
        target_step, step_inputs = self._step(
            profile, Action.INCREASE_TARGET_ROAS, historical_insufficient, data_date
        )
        return (
            Action.INCREASE_TARGET_ROAS,
            "窗口 ROAS 低于最低线，缺少预算时提高目标 ROAS。",
            self._numeric(
                snapshot.target_roas if snapshot else None,
                target_step,
                self.template.roas_quantum,
            ),
            step_inputs,
        )

    def _structure_recommendations(
        self, **context: object
    ) -> tuple[Recommendation, ...]:
        quality = context["quality"]
        profile = context["profile"]
        snapshot = context["snapshot"]
        long = context["long"]
        if (
            not isinstance(quality, DataQuality)
            or not isinstance(profile, CampaignProfile)
            or not isinstance(long, WindowStats)
            or profile.minimum_roas is None
            or not self._quality_is_complete(quality)
            or bool(context["historical_insufficient"])
            or not isinstance(snapshot, CampaignSnapshot)
            or snapshot.status is not CampaignStatus.ACTIVE
            or self._in_cooldown(
                str(context["store_id"]),
                str(context["campaign_id"]),
                context["data_date"],
                profile.cooldown_days,
            )
            or not self._budget_saturated(snapshot, long, int(context["observed_days"]))
            or not self._at_or_above_floor(long, profile)
        ):
            return ()
        items = self._item_windows(
            str(context["store_id"]),
            str(context["campaign_id"]),
            context["data_date"],
            self._short_days(profile),
        )
        if len(items) < self.template.structure_min_items:
            return ()
        winners = tuple(
            item
            for item in items.values()
            if item.roas is not None and item.roas >= profile.minimum_roas
        )
        losers = tuple(
            item
            for item in items.values()
            if item.roas is None or item.roas < profile.minimum_roas
        )
        total_gmv = sum((item.gmv for item in items.values()), Decimal(0))
        winner_gmv = sum((item.gmv for item in winners), Decimal(0))
        winner_share = Decimal(0) if total_gmv == 0 else winner_gmv / total_gmv
        item_windows = _canonical(
            {item_id: asdict(stats) for item_id, stats in sorted(items.items())}
        )
        base = dict(context)
        result: list[Recommendation] = []
        if (
            Action.CREATE_GROUP in profile.allowed_actions
            and winners
            and winner_share >= self.template.structure_winner_share
        ):
            result.append(
                self._recommendation(
                    **base,
                    action=Action.CREATE_GROUP,
                    confidence=Confidence.MEDIUM,
                    reason="商品赢家集中度达到策略门槛；创建分组前需人工核查定向与受众。",
                    numeric=None,
                    extra_inputs=(
                        ("recommendation_kind", "structure_create_group"),
                        ("item_windows", item_windows),
                        ("winner_share", str(winner_share)),
                        ("manual_check", "核查可用定向和受众；广告数据未提供该结论。"),
                    ),
                )
            )
        if Action.SPLIT_GROUP in profile.allowed_actions and winners and losers:
            result.append(
                self._recommendation(
                    **base,
                    action=Action.SPLIT_GROUP,
                    confidence=Confidence.MEDIUM,
                    reason="同活动商品已出现赢家和输家分层；拆分前需人工核查定向与受众。",
                    numeric=None,
                    extra_inputs=(
                        ("recommendation_kind", "structure_split_group"),
                        ("item_windows", item_windows),
                        ("winner_count", str(len(winners))),
                        ("loser_count", str(len(losers))),
                        ("manual_check", "核查可用定向和受众；广告数据未提供该结论。"),
                    ),
                )
            )
        return tuple(result)

    def _recommendation(self, **context: object) -> Recommendation:
        profile = context["profile"]
        snapshot = context["snapshot"]
        short = context["short"]
        long = context["long"]
        quality = context["quality"]
        action = context["action"]
        assert isinstance(profile, CampaignProfile)
        assert snapshot is None or isinstance(snapshot, CampaignSnapshot)
        assert isinstance(short, WindowStats) and isinstance(long, WindowStats)
        assert isinstance(quality, DataQuality) and isinstance(action, Action)
        inputs = self._rule_inputs(
            profile,
            int(context["profile_version"]),
            snapshot,
            tuple(context["extra_inputs"]),
        )
        input_hash = self._input_hash(
            context["data_date"],
            profile,
            int(context["profile_version"]),
            snapshot,
            quality,
            short,
            long,
            tuple(context["extra_inputs"]),
        )
        numeric = context["numeric"]
        current, proposed, ratio = (None, None, None) if numeric is None else numeric
        roas_gap = self._roas_gap(long.roas, profile.minimum_roas)
        return Recommendation(
            recommendation_id=(
                f"{context['store_id']}:{context['campaign_id']}:"
                f"{context['data_date'].isoformat()}:{action.value}"
            ),
            store_id=str(context["store_id"]),
            campaign_id=str(context["campaign_id"]),
            data_date=context["data_date"],
            action=action,
            confidence=context["confidence"],
            priority=_ACTION_PRIORITY[action],
            current_value=current,
            proposed_value=proposed,
            change_ratio=ratio,
            evidence=(
                Evidence(
                    code=action.value,
                    summary=str(context["reason"]),
                    short_window=short,
                    long_window=long,
                    quality=quality,
                    rule_inputs=inputs,
                ),
            ),
            success_condition=SuccessCondition(
                profile.minimum_roas,
                profile.minimum_gmv_growth,
                profile.evaluation_days,
            ),
            rollback_condition=RollbackCondition(
                self._rollback_floor(profile.minimum_roas), profile.rollback_days
            ),
            expected_impact=self._expected_impact(action, ratio, profile),
            risk=roas_gap,
            observation_days=profile.evaluation_days,
            rule_version=self.template.rule_version,
            input_hash=input_hash,
            revision=1,
            generated_at=context["decision_at"],
            slot={
                Action.CREATE_GROUP: RecommendationSlot.CREATE_GROUP,
                Action.SPLIT_GROUP: RecommendationSlot.SPLIT_GROUP,
            }.get(action, RecommendationSlot.PRIMARY),
        )

    def _window(
        self, store_id: str, campaign_id: str, end: date, days: int
    ) -> WindowStats:
        start = end - timedelta(days=days - 1)
        metrics = self._metrics(store_id, campaign_id, start, end, MetricGrain.CAMPAIGN)
        return WindowStats(
            start,
            end,
            sum((row.spend for row in metrics), Decimal(0)),
            sum((row.gmv for row in metrics), Decimal(0)),
            sum(row.orders for row in metrics),
        )

    def _item_windows(
        self, store_id: str, campaign_id: str, end: date, days: int
    ) -> dict[str, WindowStats]:
        start = end - timedelta(days=days - 1)
        grouped: dict[str, list[AdMetric]] = {}
        for metric in self._metrics(
            store_id, campaign_id, start, end, MetricGrain.ITEM
        ):
            grouped.setdefault(metric.item_id, []).append(metric)
        return {
            item_id: WindowStats(
                start,
                end,
                sum((row.spend for row in rows), Decimal(0)),
                sum((row.gmv for row in rows), Decimal(0)),
                sum(row.orders for row in rows),
            )
            for item_id, rows in grouped.items()
        }

    def _metrics(
        self,
        store_id: str,
        campaign_id: str,
        start: date,
        end: date,
        grain: MetricGrain,
    ) -> tuple[AdMetric, ...]:
        values: list[AdMetric] = []
        current = start
        while current <= end:
            values.extend(
                metric
                for metric in self.repository.list_metrics(
                    store_id, campaign_id, current
                )
                if metric.grain is grain
            )
            current += timedelta(days=1)
        return tuple(values)

    def _history_days(
        self, store_id: str, campaign_id: str, end: date, days: int
    ) -> frozenset[date]:
        start = end - timedelta(days=days - 1)
        return frozenset(
            metric.data_date
            for metric in self._metrics(
                store_id, campaign_id, start, end, MetricGrain.CAMPAIGN
            )
        )

    def _in_cooldown(
        self, store_id: str, campaign_id: str, data_date: date, days: int
    ) -> bool:
        if days == 0:
            return False
        previous = data_date - timedelta(days=1)
        start = data_date - timedelta(days=days)
        while previous >= start:
            if any(
                item.action in _NUMERIC_ACTIONS | {Action.PAUSE, Action.RESUME}
                for item in self.repository.list_recommendations(
                    store_id, campaign_id, previous
                )
            ):
                return True
            previous -= timedelta(days=1)
        return False

    def _short_days(self, profile: CampaignProfile) -> int:
        return self.template.short_window_days

    def _long_days(self, profile: CampaignProfile) -> int:
        return self.template.long_window_days

    def _quality_is_complete(self, quality: DataQuality) -> bool:
        return (
            quality.status is DataQualityStatus.HEALTHY and not quality.missing_fields
        )

    def _at_or_above_floor(self, stats: WindowStats, profile: CampaignProfile) -> bool:
        return (
            stats.roas is not None
            and profile.minimum_roas is not None
            and stats.roas >= profile.minimum_roas
        )

    def _budget_saturated(
        self, snapshot: CampaignSnapshot | None, stats: WindowStats, observed_days: int
    ) -> bool:
        if (
            snapshot is None
            or snapshot.current_budget is None
            or snapshot.current_budget == 0
            or observed_days <= 0
        ):
            return False
        days = Decimal((stats.end_date - stats.start_date).days + 1)
        return (
            stats.spend / (snapshot.current_budget * days)
            >= self.template.min_budget_utilization
        )

    def _step(
        self,
        profile: CampaignProfile,
        action: Action,
        low_confidence: bool,
        data_date: date,
    ) -> tuple[Decimal, tuple[tuple[str, str], ...]]:
        configured = (
            profile.max_budget_step
            if action in {Action.INCREASE_BUDGET, Action.DECREASE_BUDGET}
            else profile.max_target_roas_step
        )
        global_limit = (
            self.template.global_max_budget_step
            if action in {Action.INCREASE_BUDGET, Action.DECREASE_BUDGET}
            else self.template.global_max_target_roas_step
        )
        base = self.template.default_budget_step
        step = min(base * profile.growth_intensity, configured, global_limit)
        if low_confidence:
            step = min(step, self.template.low_confidence_budget_step)
        summary = self.effectiveness_provider and self.effectiveness_provider(
            profile.store_id, profile.campaign_id, data_date, action
        )
        calibrated: Decimal | None = None
        if (
            isinstance(summary, EffectivenessSummary)
            and summary.sample_size >= self.template.effectiveness_min_samples
        ):
            calibrated = (
                summary.budget_step
                if action in {Action.INCREASE_BUDGET, Action.DECREASE_BUDGET}
                else summary.target_roas_step
            )
        if isinstance(calibrated, Decimal) and calibrated.is_finite():
            step = min(calibrated, configured, global_limit)
        if low_confidence:
            step = min(step, self.template.low_confidence_budget_step)
        inputs = (
            ("default_step", _decimal_text(base)),
            ("growth_intensity", _decimal_text(profile.growth_intensity)),
            (
                "sample_count",
                "0" if not isinstance(summary, EffectivenessSummary) else str(summary.sample_size),
            ),
            (
                "success_rate",
                "none"
                if not isinstance(summary, EffectivenessSummary)
                or summary.success_rate is None
                else _decimal_text(summary.success_rate),
            ),
            (
                "recommended_step",
                "none" if calibrated is None else _decimal_text(calibrated),
            ),
            ("effective_step", _decimal_text(step)),
        )
        return step, inputs

    def _numeric(
        self, current: Decimal | None, ratio: Decimal, quantum: Decimal
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        if current is None or current <= 0 or ratio == 0:
            return None
        proposed = (current * (Decimal(1) + ratio)).quantize(
            quantum, rounding=ROUND_HALF_UP
        )
        if proposed <= 0 or proposed == current:
            return None
        return current, proposed, proposed / current - Decimal(1)

    def _rollback_floor(self, floor: Decimal | None) -> Decimal | None:
        if floor is None:
            return None
        return floor * (Decimal(1) - self.template.floor_guard_band)

    def _expected_impact(
        self, action: Action, ratio: Decimal | None, profile: CampaignProfile
    ) -> Decimal:
        if ratio is not None:
            return abs(ratio)
        if action in {Action.CREATE_GROUP, Action.SPLIT_GROUP}:
            return profile.minimum_gmv_growth
        return Decimal(0)

    def _roas_gap(self, roas: Decimal | None, floor: Decimal | None) -> Decimal:
        if roas is None or floor is None:
            return Decimal(1)
        return min(abs(roas - floor) / floor, Decimal(1))

    def _rule_inputs(
        self,
        profile: CampaignProfile,
        version: int,
        snapshot: CampaignSnapshot | None,
        extra: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        pairs = (
            ("policy_template", _canonical(asdict(self.template))),
            ("profile", _canonical(asdict(profile))),
            ("profile_version", str(version)),
            (
                "snapshot",
                _canonical(
                    None
                    if snapshot is None
                    else {
                        key: value
                        for key, value in asdict(snapshot).items()
                        if key != "observed_at"
                    }
                ),
            ),
        ) + extra
        return tuple(sorted(pairs))

    def _input_hash(
        self,
        data_date: object,
        profile: CampaignProfile,
        version: int,
        snapshot: CampaignSnapshot | None,
        quality: DataQuality,
        short: WindowStats,
        long: WindowStats,
        extra: tuple[tuple[str, str], ...],
    ) -> str:
        value = {
            "data_date": data_date,
            "profile": asdict(profile),
            "profile_version": version,
            "snapshot": None if snapshot is None else {
                key: value for key, value in asdict(snapshot).items() if key != "observed_at"
            },
            "quality": {
                key: value for key, value in asdict(quality).items() if key != "assessed_at"
            },
            "short_window": asdict(short),
            "long_window": asdict(long),
            "campaign_metrics": [
                {key: value for key, value in asdict(metric).items() if key != "collected_at"}
                for metric in self._metrics(
                    profile.store_id,
                    profile.campaign_id,
                    long.start_date,
                    long.end_date,
                    MetricGrain.CAMPAIGN,
                )
            ],
            "template": asdict(self.template),
            "extra": extra,
        }
        return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value, default=_json_default, sort_keys=True, separators=(",", ":")
    )


def _decimal_text(value: Decimal) -> str:
    """把数值等价的 Decimal 固化为同一条策略输入文本。"""
    return str(value.normalize())


def _json_default(value: object) -> str | list[str]:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(
        value,
        (
            CampaignStatus,
            DataQualityStatus,
            ProfileStatus,
            Action,
            Confidence,
            MetricGrain,
        ),
    ):
        return value.value
    if isinstance(value, frozenset):
        return sorted(
            item.value if isinstance(item, Action) else str(item) for item in value
        )
    raise TypeError(f"无法序列化策略输入：{type(value)!r}")

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

# 合法状态转换表（模块级常量，避免 str Enum 对单下划线前缀的特殊处理）
_EXPERIMENT_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("running", "cancelled"),
    "running": ("paused", "completed"),
    "paused": ("running", "cancelled"),
    "completed": (),
    "reverted": (),
    "cancelled": (),
}


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    REVERTED = "reverted"
    CANCELLED = "cancelled"

    def can_transition_to(self, target: ExperimentStatus) -> bool:
        """判断当前状态是否可以转换到目标状态。"""
        return target.value in _EXPERIMENT_STATUS_TRANSITIONS.get(self.value, ())


class VariantType(str, Enum):
    CONTROL = "control"
    TREATMENT = "treatment"


class VariantStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    REMOVED = "removed"


class GuardrailSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Experiment:
    id: str
    name: str
    description: str
    platform: str
    store: str
    status: ExperimentStatus
    starts_on: date
    ends_on: date
    guardrail_config_json: str
    created_by: str
    created_at: str
    notes: str


@dataclass(frozen=True)
class ExperimentVariant:
    id: str
    experiment_id: str
    variant_type: VariantType
    name: str
    strategy_params_json: str
    traffic_percent: float
    status: VariantStatus
    created_at: str


@dataclass(frozen=True)
class ExperimentWindow:
    id: str
    experiment_id: str
    data_date: str
    variant_id: str
    metrics_json: str
    guardrail_breaches_json: str
    created_at: str


@dataclass(frozen=True)
class ExperimentGuardrailBreach:
    id: str
    experiment_id: str
    window_id: str | None
    metric_name: str
    threshold: float
    actual_value: float
    severity: str
    occurred_at: str
    resolved: int


@dataclass(frozen=True)
class CollectorHealth:
    collector_name: str
    platform: str
    healthy: int
    last_checked_at: str
    consecutive_failures: int
    consecutive_successes: int
    failover_active: int
    failover_started_at: str | None


@dataclass
class ExperimentGuardrailConfig:
    max_daily_spend: Decimal | None = None
    max_roas_drop_pct: float | None = None
    max_daily_alert_count: int | None = None
    min_data_days: int | None = None

    @classmethod
    def from_json(cls, raw: str) -> ExperimentGuardrailConfig:
        import json

        data = json.loads(raw) if raw and raw != "{}" else {}
        return cls(
            max_daily_spend=(
                Decimal(str(data["max_daily_spend"]))
                if "max_daily_spend" in data
                else None
            ),
            max_roas_drop_pct=data.get("max_roas_drop_pct"),
            max_daily_alert_count=data.get("max_daily_alert_count"),
            min_data_days=data.get("min_data_days"),
        )

    def to_json(self) -> str:
        import json

        data: dict[str, Any] = {}
        if self.max_daily_spend is not None:
            data["max_daily_spend"] = str(self.max_daily_spend)
        if self.max_roas_drop_pct is not None:
            data["max_roas_drop_pct"] = self.max_roas_drop_pct
        if self.max_daily_alert_count is not None:
            data["max_daily_alert_count"] = self.max_daily_alert_count
        if self.min_data_days is not None:
            data["min_data_days"] = self.min_data_days
        return json.dumps(data, sort_keys=True)


def choose_variant(
    store_id: str, variants: list[ExperimentVariant]
) -> ExperimentVariant | None:
    """按 store_id 哈希分桶选择变体。

    同一 store_id 始终分配到同一变体。
    """
    if not variants:
        return None
    if len(variants) == 1:
        return variants[0]
    # 取 hash 绝对值后按流量比例分配
    hash_val = abs(hash(store_id)) % 100
    cumulative = 0.0
    for variant in variants:
        cumulative += variant.traffic_percent
        if hash_val < cumulative:
            return variant
    return variants[-1]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
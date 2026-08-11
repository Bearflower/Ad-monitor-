from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from adwatch.domain import DailyAdMetric
from adwatch.experiments.guardrails import (
    GuardrailEvaluation,
    evaluate_guardrails,
)
from adwatch.experiments.models import (
    Experiment,
    ExperimentGuardrailConfig,
    ExperimentStatus,
    ExperimentVariant,
    VariantType,
    choose_variant,
)
from adwatch.experiments.registry import ExperimentRepository
from adwatch.storage.db import Database


class ExperimentService:
    """实验管理服务。"""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.repository = ExperimentRepository(database)

    def create_experiment(
        self,
        name: str,
        description: str,
        platform: str,
        store: str,
        starts_on: date,
        ends_on: date,
        guardrail_config: ExperimentGuardrailConfig | None = None,
        created_by: str = "system",
        notes: str = "",
    ) -> Experiment:
        guardrail_json = (
            guardrail_config.to_json()
            if guardrail_config
            else "{}"
        )
        experiment = self.repository.create_experiment(
            name=name,
            description=description,
            platform=platform,
            store=store,
            starts_on=starts_on,
            ends_on=ends_on,
            guardrail_config_json=guardrail_json,
            created_by=created_by,
            notes=notes,
        )
        self._record_audit(
            object_type="experiment",
            object_id=experiment.id,
            action="experiment.created",
            after_json={
                "name": name,
                "platform": platform,
                "store": store,
                "starts_on": starts_on.isoformat(),
                "ends_on": ends_on.isoformat(),
            },
            actor=created_by,
            reason="创建实验",
        )
        return experiment

    def add_variant(
        self,
        experiment_id: str,
        variant_type: VariantType,
        name: str,
        strategy_params: dict[str, Any] | None = None,
        traffic_percent: float = 50.0,
    ) -> ExperimentVariant:
        import json

        strategy_json = json.dumps(
            strategy_params or {}, sort_keys=True
        )
        variant = self.repository.add_variant(
            experiment_id=experiment_id,
            variant_type=variant_type,
            name=name,
            strategy_params_json=strategy_json,
            traffic_percent=traffic_percent,
        )
        self._record_audit(
            object_type="experiment",
            object_id=experiment_id,
            action="experiment.variant_added",
            after_json={
                "variant_id": variant.id,
                "name": variant.name,
                "type": variant.variant_type.value,
                "traffic_pct": variant.traffic_percent,
            },
            actor="system",
            reason=f"添加变体: {name}",
        )
        return variant

    def start_experiment(
        self, experiment_id: str, actor: str = "system"
    ) -> Experiment | None:
        result = self.repository.update_experiment_status(
            experiment_id, ExperimentStatus.RUNNING
        )
        if result:
            self._record_audit(
                object_type="experiment",
                object_id=experiment_id,
                action="experiment.started",
                actor=actor,
                reason="启动实验",
            )
        return result

    def pause_experiment(
        self, experiment_id: str, actor: str = "system"
    ) -> Experiment | None:
        result = self.repository.update_experiment_status(
            experiment_id, ExperimentStatus.PAUSED
        )
        if result:
            self._record_audit(
                object_type="experiment",
                object_id=experiment_id,
                action="experiment.paused",
                actor=actor,
                reason="暂停实验",
            )
        return result

    def complete_experiment(
        self, experiment_id: str, actor: str = "system"
    ) -> Experiment | None:
        result = self.repository.update_experiment_status(
            experiment_id, ExperimentStatus.COMPLETED
        )
        if result:
            self._record_audit(
                object_type="experiment",
                object_id=experiment_id,
                action="experiment.completed",
                actor=actor,
                reason="完成实验",
            )
        return result

    def cancel_experiment(
        self, experiment_id: str, actor: str = "system"
    ) -> Experiment | None:
        result = self.repository.update_experiment_status(
            experiment_id, ExperimentStatus.CANCELLED
        )
        if result:
            self._record_audit(
                object_type="experiment",
                object_id=experiment_id,
                action="experiment.cancelled",
                actor=actor,
                reason="取消实验",
            )
        return result

    def evaluate_experiment_day(
        self,
        experiment_id: str,
        data_date: date,
        metrics: list[DailyAdMetric],
        *,
        previous_roas: float | None = None,
        alert_count: int = 0,
        consecutive_data_days: int = 0,
    ) -> GuardrailEvaluation:
        """评估实验某天的护栏条件。"""
        from decimal import Decimal

        experiment = self.repository.get_experiment(experiment_id)
        if experiment is None:
            raise ValueError(f"Experiment not found: {experiment_id}")

        config = ExperimentGuardrailConfig.from_json(
            experiment.guardrail_config_json
        )
        previous_roas_dec = (
            Decimal(str(previous_roas))
            if previous_roas is not None
            else None
        )
        evaluation = evaluate_guardrails(
            experiment_id=experiment_id,
            guardrail_config=config,
            metrics=metrics,
            data_date=data_date,
            previous_roas=previous_roas_dec,
            alert_count=alert_count,
            consecutive_data_days=consecutive_data_days,
        )
        if not evaluation.passed:
            for breach in evaluation.breaches:
                self.repository.record_breach(breach)
        return evaluation

    def assign_variant(
        self, store_id: str, experiment_id: str
    ) -> ExperimentVariant | None:
        """为店铺分配实验变体。"""
        variants = self.repository.get_variants(experiment_id)
        if not variants:
            return None
        variant = choose_variant(store_id, variants)
        if variant:
            self._record_audit(
                object_type="experiment",
                object_id=experiment_id,
                action="variant.assigned",
                after_json={
                    "store_id": store_id,
                    "variant_id": variant.id,
                    "variant_name": variant.name,
                    "variant_type": variant.variant_type.value,
                },
                actor="system",
                reason=f"店铺 {store_id} 分配到变体 {variant.name}",
            )
        return variant

    def list_experiments(
        self,
        *,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Experiment]:
        return self.repository.list_experiments(
            platform=platform, status=status, limit=limit
        )

    def get_experiment(
        self, experiment_id: str
    ) -> Experiment | None:
        return self.repository.get_experiment(experiment_id)

    def get_variants(
        self, experiment_id: str
    ) -> list[ExperimentVariant]:
        return self.repository.get_variants(experiment_id)

    def get_breaches(
        self, experiment_id: str
    ) -> list[Any]:
        return self.repository.get_breaches(experiment_id)

    def _record_audit(
        self,
        *,
        object_type: str,
        object_id: str,
        action: str,
        actor: str = "system",
        reason: str = "",
        before_json: dict[str, Any] | None = None,
        after_json: dict[str, Any] | None = None,
    ) -> None:
        import json

        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, object_type, object_id, action,
                    before_json, after_json, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    object_type,
                    object_id,
                    action,
                    json.dumps(before_json, sort_keys=True)
                    if before_json
                    else None,
                    json.dumps(after_json, sort_keys=True)
                    if after_json
                    else None,
                    actor,
                    reason,
                    datetime.now(UTC).isoformat(),
                ),
            )
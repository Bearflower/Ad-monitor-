from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from adwatch.experiments.models import (
    Experiment,
    ExperimentGuardrailBreach,
    ExperimentStatus,
    ExperimentVariant,
    ExperimentWindow,
    VariantStatus,
    VariantType,
    _utc_now,
)


@dataclass
class ExperimentRepository:
    """实验仓储：直接操作 SQLite 数据库。"""

    database: Any  # Database 实例

    def create_experiment(
        self,
        name: str,
        description: str,
        platform: str,
        store: str,
        starts_on: date,
        ends_on: date,
        guardrail_config_json: str,
        created_by: str,
        notes: str = "",
    ) -> Experiment:
        experiment = Experiment(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            platform=platform,
            store=store,
            status=ExperimentStatus.DRAFT,
            starts_on=starts_on,
            ends_on=ends_on,
            guardrail_config_json=guardrail_config_json,
            created_by=created_by,
            created_at=_utc_now(),
            notes=notes,
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO experiments(
                    id, name, description, platform, store, status,
                    starts_on, ends_on, guardrail_config_json,
                    created_by, created_at, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment.id,
                    experiment.name,
                    experiment.description,
                    experiment.platform,
                    experiment.store,
                    experiment.status.value,
                    experiment.starts_on.isoformat(),
                    experiment.ends_on.isoformat(),
                    experiment.guardrail_config_json,
                    experiment.created_by,
                    experiment.created_at,
                    experiment.notes,
                ),
            )
        return experiment

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE id=?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_experiment(row)

    def list_experiments(
        self,
        *,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Experiment]:
        parts: list[str] = ["SELECT * FROM experiments WHERE 1=1"]
        params: list[Any] = []
        if platform:
            parts.append("AND platform=?")
            params.append(platform)
        if status:
            parts.append("AND status=?")
            params.append(status)
        parts.append("ORDER BY created_at DESC LIMIT ?")
        params.append(limit)
        with self.database.transaction() as connection:
            rows = connection.execute(
                " ".join(parts), params
            ).fetchall()
            return [self._row_to_experiment(r) for r in rows]

    def update_experiment_status(
        self, experiment_id: str, new_status: ExperimentStatus
    ) -> Experiment | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE id=?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                return None
            current = ExperimentStatus(row["status"])
            if not current.can_transition_to(new_status):
                raise ValueError(
                    f"Cannot transition from {current.value} "
                    f"to {new_status.value}"
                )
            connection.execute(
                "UPDATE experiments SET status=? WHERE id=?",
                (new_status.value, experiment_id),
            )
            # 在同一连接中读取更新后的行，避免新连接看不到未提交的变更
            updated_row = connection.execute(
                "SELECT * FROM experiments WHERE id=?",
                (experiment_id,),
            ).fetchone()
            if updated_row is None:
                return None
            return self._row_to_experiment(updated_row)

    def add_variant(
        self,
        experiment_id: str,
        variant_type: VariantType,
        name: str,
        strategy_params_json: str,
        traffic_percent: float,
    ) -> ExperimentVariant:
        variant = ExperimentVariant(
            id=str(uuid.uuid4()),
            experiment_id=experiment_id,
            variant_type=variant_type,
            name=name,
            strategy_params_json=strategy_params_json,
            traffic_percent=traffic_percent,
            status=VariantStatus.ACTIVE,
            created_at=_utc_now(),
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO experiment_variants(
                    id, experiment_id, variant_type, name,
                    strategy_params_json, traffic_percent, status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    variant.id,
                    variant.experiment_id,
                    variant.variant_type.value,
                    variant.name,
                    variant.strategy_params_json,
                    variant.traffic_percent,
                    variant.status.value,
                    variant.created_at,
                ),
            )
        return variant

    def get_variants(
        self, experiment_id: str
    ) -> list[ExperimentVariant]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM experiment_variants "
                "WHERE experiment_id=? ORDER BY created_at",
                (experiment_id,),
            ).fetchall()
            return [self._row_to_variant(r) for r in rows]

    def record_window(
        self,
        experiment_id: str,
        data_date: str,
        variant_id: str,
        metrics_json: str,
        guardrail_breaches_json: str,
    ) -> ExperimentWindow:
        window = ExperimentWindow(
            id=str(uuid.uuid4()),
            experiment_id=experiment_id,
            data_date=data_date,
            variant_id=variant_id,
            metrics_json=metrics_json,
            guardrail_breaches_json=guardrail_breaches_json,
            created_at=_utc_now(),
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO experiment_windows(
                    id, experiment_id, data_date, variant_id,
                    metrics_json, guardrail_breaches_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    window.id,
                    window.experiment_id,
                    window.data_date,
                    window.variant_id,
                    window.metrics_json,
                    window.guardrail_breaches_json,
                    window.created_at,
                ),
            )
        return window

    def record_breach(
        self,
        breach: ExperimentGuardrailBreach,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO experiment_guardrail_breaches(
                    id, experiment_id, window_id, metric_name,
                    threshold, actual_value, severity, occurred_at,
                    resolved
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    breach.id,
                    breach.experiment_id,
                    breach.window_id,
                    breach.metric_name,
                    breach.threshold,
                    breach.actual_value,
                    breach.severity,
                    breach.occurred_at,
                    breach.resolved,
                ),
            )

    def get_breaches(
        self, experiment_id: str
    ) -> list[ExperimentGuardrailBreach]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM experiment_guardrail_breaches "
                "WHERE experiment_id=? ORDER BY occurred_at DESC",
                (experiment_id,),
            ).fetchall()
            return [self._row_to_breach(r) for r in rows]

    @staticmethod
    def _row_to_experiment(row: Any) -> Experiment:
        return Experiment(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            platform=row["platform"],
            store=row["store"],
            status=ExperimentStatus(row["status"]),
            starts_on=date.fromisoformat(row["starts_on"]),
            ends_on=date.fromisoformat(row["ends_on"]),
            guardrail_config_json=row["guardrail_config_json"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            notes=row["notes"],
        )

    @staticmethod
    def _row_to_variant(row: Any) -> ExperimentVariant:
        return ExperimentVariant(
            id=row["id"],
            experiment_id=row["experiment_id"],
            variant_type=VariantType(row["variant_type"]),
            name=row["name"],
            strategy_params_json=row["strategy_params_json"],
            traffic_percent=row["traffic_percent"],
            status=VariantStatus(row["status"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_breach(
        row: Any,
    ) -> ExperimentGuardrailBreach:
        return ExperimentGuardrailBreach(
            id=row["id"],
            experiment_id=row["experiment_id"],
            window_id=row["window_id"],
            metric_name=row["metric_name"],
            threshold=row["threshold"],
            actual_value=row["actual_value"],
            severity=row["severity"],
            occurred_at=row["occurred_at"],
            resolved=row["resolved"],
        )
"""优化器领域模型的持久化仓储。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

from adwatch.optimizer.database import (
    _METRIC_OBSERVATION_INSERT_IGNORE,
    COLLECTION_CLAIM_SENTINEL,
    LEGACY_COLLECTION_CLAIM_NOTE,
    OptimizerDatabase,
    _datetime_epoch_us,
    _metric_values,
)
from adwatch.optimizer.models import (
    Action,
    AdMetric,
    CampaignProfile,
    CampaignSnapshot,
    CampaignStatus,
    Confidence,
    DataQuality,
    DataQualityStatus,
    Evaluation,
    EvaluationStatus,
    Evidence,
    Feedback,
    FeedbackStatus,
    MetricGrain,
    ProfileStatus,
    Recommendation,
    RecommendationSlot,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)


@dataclass(frozen=True)
class RunLog:
    run_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    details: dict[str, object] | None = None


@dataclass(frozen=True)
class CollectionDay:
    """一个店铺业务日的采集状态及 no_data 复查时间。"""

    store_id: str
    data_date: date
    status: str
    attempted_at: datetime | None = None
    next_retry_at: datetime | None = None
    claim_token: str | None = None
    claim_until: datetime | None = None


@dataclass(frozen=True)
class EvaluationRecord:
    recommendation: Recommendation
    evaluation: Evaluation


@dataclass(frozen=True)
class StoreDayReadSnapshot:
    """日报在一个 SQLite 只读事务中冻结的店铺事实。"""

    collection: CollectionDay | None
    actual_data_date: date | None
    metrics: tuple[AdMetric, ...]
    recommendations: tuple[Recommendation, ...]
    pending_feedback: tuple[Feedback, ...]


@dataclass(frozen=True)
class CampaignReadModel:
    """广告活动页在同一截止时点可见的批量读模型。"""

    campaign_id: str
    snapshot: CampaignSnapshot | None
    metric: AdMetric | None
    profile: CampaignProfile | None
    profile_versions: tuple[CampaignProfile, ...]


@dataclass(frozen=True)
class ReviewReadModel:
    """复盘页的一条反馈、精确建议、最新评估与同类统计。"""

    feedback: Feedback
    recommendation: Recommendation
    evaluation: Evaluation | None
    total_recommendations: int
    feedback_count: int
    adopted_count: int
    completed_evaluation_count: int
    effective_count: int
    rollback_triggered: bool | None


class ClaimLost(RuntimeError):
    """写入时发现业务日已被其他回填 owner 领取。"""


_LATEST_METRIC_DATE_AS_OF_SQL = """SELECT candidate.data_date
FROM optimizer_ad_metric_observations AS candidate
     INDEXED BY optimizer_observation_report_date
WHERE candidate.store_id=? AND candidate.grain='campaign'
  AND candidate.data_date<=? AND candidate.collected_at_us<=?
  AND candidate.is_deleted=0
  AND NOT EXISTS (
      SELECT 1
      FROM optimizer_ad_metric_observations AS newer
           INDEXED BY optimizer_observation_logical_latest
      WHERE newer.store_id=candidate.store_id
        AND newer.campaign_id=candidate.campaign_id
        AND newer.item_id=candidate.item_id
        AND newer.data_date=candidate.data_date
        AND newer.grain=candidate.grain
        AND newer.collected_at_us<=?
        AND (
            newer.collected_at_us>candidate.collected_at_us
            OR (
                newer.collected_at_us=candidate.collected_at_us
                AND newer.row_id>candidate.row_id
            )
        )
  )
ORDER BY candidate.data_date DESC
LIMIT 1"""


_CAMPAIGN_METRICS_AS_OF_SQL = """SELECT observation.*
FROM optimizer_ad_metric_observations AS observation
     INDEXED BY optimizer_observation_campaign_recent
WHERE observation.store_id=? AND observation.grain='campaign'
  AND observation.data_date>=? AND observation.data_date<=?
  AND observation.collected_at_us<=?
  AND observation.is_deleted=0
  AND NOT EXISTS (
      SELECT 1
      FROM optimizer_ad_metric_observations AS newer
           INDEXED BY optimizer_observation_logical_latest
      WHERE newer.store_id=observation.store_id
        AND newer.campaign_id=observation.campaign_id
        AND newer.item_id=observation.item_id
        AND newer.data_date=observation.data_date
        AND newer.grain=observation.grain
        AND newer.collected_at_us<=?
        AND (
            newer.collected_at_us>observation.collected_at_us
            OR (
                newer.collected_at_us=observation.collected_at_us
                AND newer.row_id>observation.row_id
            )
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM optimizer_ad_metric_observations AS candidate
           INDEXED BY optimizer_observation_campaign_recent
      WHERE candidate.store_id=observation.store_id
        AND candidate.grain=observation.grain
        AND candidate.campaign_id=observation.campaign_id
        AND candidate.data_date>=? AND candidate.data_date<=?
        AND candidate.collected_at_us<=?
        AND candidate.is_deleted=0
        AND (
            candidate.data_date>observation.data_date
            OR (
                candidate.data_date=observation.data_date
                AND candidate.collected_at_us>observation.collected_at_us
            )
            OR (
                candidate.data_date=observation.data_date
                AND candidate.collected_at_us=observation.collected_at_us
                AND candidate.row_id>observation.row_id
            )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM optimizer_ad_metric_observations AS candidate_newer
                 INDEXED BY optimizer_observation_logical_latest
            WHERE candidate_newer.store_id=candidate.store_id
              AND candidate_newer.campaign_id=candidate.campaign_id
              AND candidate_newer.item_id=candidate.item_id
              AND candidate_newer.data_date=candidate.data_date
              AND candidate_newer.grain=candidate.grain
              AND candidate_newer.collected_at_us<=?
              AND (
                  candidate_newer.collected_at_us>candidate.collected_at_us
                  OR (
                      candidate_newer.collected_at_us=candidate.collected_at_us
                      AND candidate_newer.row_id>candidate.row_id
                  )
              )
        )
  )
"""


_PENDING_FEEDBACK_SQL = """SELECT feedback.*, recommendation.recommendation_id,
       recommendation.revision AS recommendation_revision
FROM optimizer_recommendations AS recommendation
JOIN optimizer_recommendation_feedback AS feedback
  ON feedback.recommendation_row_id=recommendation.row_id
LEFT JOIN optimizer_recommendation_evaluations AS latest
  ON latest.evaluation_id=(
      SELECT candidate.evaluation_id
      FROM optimizer_recommendation_evaluations AS candidate
      WHERE candidate.recommendation_row_id=feedback.recommendation_row_id
        AND candidate.feedback_id=feedback.feedback_id
      ORDER BY candidate.revision DESC
      LIMIT 1
  )
WHERE recommendation.store_id=?
  AND feedback.status IN ('executed','modified')
  AND (latest.status IS NULL OR latest.status NOT IN (
      'effective','no_change','negative'
  ))
ORDER BY feedback.executed_at_us,feedback.feedback_id"""


class OptimizerRepository:
    def __init__(self, database: OptimizerDatabase) -> None:
        self.database = database

    def upsert_metric(self, metric: AdMetric) -> AdMetric:
        with self.database.transaction() as connection:
            connection.execute(
                _METRIC_OBSERVATION_INSERT_IGNORE,
                (*_metric_values(metric), 0),
            )
            connection.execute(
                """INSERT INTO optimizer_ad_metrics(
                    store_id, campaign_id, campaign_name, item_id, data_date, currency,
                    spend, gmv, orders, collected_at, collected_at_us, source, grain
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, campaign_id, item_id, data_date, grain) DO UPDATE SET
                    campaign_name=excluded.campaign_name, currency=excluded.currency,
                    spend=excluded.spend, gmv=excluded.gmv, orders=excluded.orders,
                    collected_at=excluded.collected_at,
                    collected_at_us=excluded.collected_at_us, source=excluded.source,
                    grain=excluded.grain
                WHERE excluded.collected_at_us >= optimizer_ad_metrics.collected_at_us""",
                _metric_values(metric),
            )
        return metric

    def list_metrics(
        self, store_id: str, campaign_id: str, data_date: date
    ) -> tuple[AdMetric, ...]:
        _require_business_date(data_date, "data_date")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM optimizer_ad_metrics WHERE store_id=? AND campaign_id=? AND data_date=? ORDER BY item_id, grain",
                (store_id, campaign_id, data_date.isoformat()),
            ).fetchall()
        return tuple(_metric_from_row(row) for row in rows)

    def list_campaign_metrics_between(
        self,
        store_id: str,
        campaign_id: str,
        start: date,
        end: date,
        *,
        collected_by: datetime,
        data_through: date | None = None,
    ) -> tuple[AdMetric, ...]:
        """读取评估时点已经存在的活动粒度日事实。"""
        _require_business_date(start, "start")
        _require_business_date(end, "end")
        if start > end:
            raise ValueError("start must not be after end")
        if collected_by.tzinfo is None or collected_by.utcoffset() is None:
            raise ValueError("collected_by must include timezone information")
        if data_through is not None:
            _require_business_date(data_through, "data_through")
        visible_end = min(end, data_through) if data_through is not None else end
        if visible_end < start:
            return ()
        with self.database.connect() as connection:
            rows = connection.execute(
                """WITH visible AS (
                    SELECT observation.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY store_id,campaign_id,item_id,data_date,grain
                               ORDER BY collected_at_us DESC, row_id DESC
                           ) AS visible_rank
                    FROM optimizer_ad_metric_observations AS observation
                    WHERE store_id=? AND campaign_id=?
                      AND data_date BETWEEN ? AND ?
                      AND collected_at_us<=?
                )
                SELECT * FROM visible
                WHERE visible_rank=1 AND is_deleted=0 AND grain='campaign'
                ORDER BY data_date, item_id""",
                (
                    store_id,
                    campaign_id,
                    start.isoformat(),
                    visible_end.isoformat(),
                    _datetime_epoch_us(collected_by.astimezone(UTC)),
                ),
            ).fetchall()
        return tuple(_metric_from_row(row) for row in rows)

    def campaign_totals(
        self, store_id: str, data_date: date
    ) -> tuple[Decimal, Decimal, int]:
        _require_business_date(data_date, "data_date")
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT spend, gmv, orders FROM optimizer_ad_metrics
                   WHERE store_id=? AND data_date=? AND grain='campaign'""",
                (store_id, data_date.isoformat()),
            ).fetchall()
        spend = sum((Decimal(row["spend"]) for row in rows), Decimal(0))
        gmv = sum((Decimal(row["gmv"]) for row in rows), Decimal(0))
        orders = sum(int(row["orders"]) for row in rows)
        return (spend, gmv, orders)

    def latest_metric_date_as_of(
        self, store_id: str, data_through: date, *, collected_by: datetime
    ) -> date | None:
        """返回截止时点可见的最近活动粒度数据日。"""
        _require_business_date(data_through, "data_through")
        if collected_by.tzinfo is None or collected_by.utcoffset() is None:
            raise ValueError("collected_by must include timezone information")
        cutoff = _datetime_epoch_us(collected_by.astimezone(UTC))
        with self.database.connect() as connection:
            return _latest_metric_date_as_of_connection(
                connection, store_id, data_through, cutoff
            )

    def campaign_metrics_for_store_day_as_of(
        self, store_id: str, data_date: date, *, collected_by: datetime
    ) -> tuple[AdMetric, ...]:
        """返回一个店铺数据日在截止时点可见的活动事实。"""
        _require_business_date(data_date, "data_date")
        if collected_by.tzinfo is None or collected_by.utcoffset() is None:
            raise ValueError("collected_by must include timezone information")
        with self.database.connect() as connection:
            return _campaign_metrics_for_store_day_as_of_connection(
                connection,
                store_id,
                data_date,
                _datetime_epoch_us(collected_by.astimezone(UTC)),
            )

    def report_snapshot_as_of(
        self, store_id: str, expected_data_date: date, decision_at: datetime
    ) -> StoreDayReadSnapshot:
        """在单个 deferred 只读事务中冻结日报所需的全部事实。"""
        _require_business_date(expected_data_date, "expected_data_date")
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        cutoff = _datetime_epoch_us(decision_at.astimezone(UTC))
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            try:
                collection = _collection_day_as_of_connection(
                    connection, store_id, expected_data_date, cutoff
                )
                actual_data_date = _latest_metric_date_as_of_connection(
                    connection, store_id, expected_data_date, cutoff
                )
                metrics = (
                    ()
                    if actual_data_date is None
                    else _campaign_metrics_for_store_day_as_of_connection(
                        connection, store_id, actual_data_date, cutoff
                    )
                )
                recommendations = (
                    ()
                    if actual_data_date is None
                    else _latest_recommendations_for_store_day_as_of_connection(
                        connection,
                        store_id,
                        actual_data_date,
                        decision_at.astimezone(UTC),
                    )
                )
                pending = _pending_feedback_as_of_connection(
                    connection, store_id, cutoff
                )
                return StoreDayReadSnapshot(
                    collection,
                    actual_data_date,
                    metrics,
                    recommendations,
                    pending,
                )
            finally:
                connection.rollback()

    def campaign_read_models_as_of(
        self,
        store_id: str,
        *,
        recent_start: date,
        data_through: date,
        decision_at: datetime,
    ) -> tuple[CampaignReadModel, ...]:
        """批量读取活动、最新控制面、最近指标及当时可见的档案历史。"""
        _require_business_date(recent_start, "recent_start")
        _require_business_date(data_through, "data_through")
        if recent_start > data_through:
            raise ValueError("recent_start must not be after data_through")
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        cutoff = _datetime_epoch_us(decision_at.astimezone(UTC))
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            try:
                metric_rows = connection.execute(
                    _CAMPAIGN_METRICS_AS_OF_SQL,
                    (
                        store_id,
                        recent_start.isoformat(),
                        data_through.isoformat(),
                        cutoff,
                        cutoff,
                        recent_start.isoformat(),
                        data_through.isoformat(),
                        cutoff,
                        cutoff,
                    ),
                ).fetchall()
                snapshot_rows = connection.execute(
                    """WITH ranked AS (
                        SELECT snapshot.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY campaign_id
                                   ORDER BY data_date DESC,observed_at_us DESC,rowid DESC
                               ) AS latest_rank
                        FROM optimizer_campaign_snapshots AS snapshot
                        WHERE store_id=? AND data_date<=? AND observed_at_us<=?
                    )
                    SELECT * FROM ranked WHERE latest_rank=1 ORDER BY campaign_id""",
                    (store_id, data_through.isoformat(), cutoff),
                ).fetchall()
                profile_rows = connection.execute(
                    """SELECT campaign_id,version,payload_json
                    FROM optimizer_campaign_profile_versions
                    WHERE store_id=? AND created_at<=?
                    ORDER BY campaign_id,version""",
                    (store_id, data_through.isoformat()),
                ).fetchall()
            finally:
                connection.rollback()
        metrics = {
            row["campaign_id"]: _metric_from_row(row) for row in metric_rows
        }
        snapshots = {
            row["campaign_id"]: _snapshot_from_row(row) for row in snapshot_rows
        }
        versioned_profiles: dict[str, list[tuple[int, CampaignProfile]]] = {}
        for row in profile_rows:
            versioned_profiles.setdefault(row["campaign_id"], []).append(
                (int(row["version"]), _profile_from_payload(row["payload_json"]))
            )
        versions = {
            campaign_id: tuple(profile for _, profile in items)
            for campaign_id, items in versioned_profiles.items()
        }
        current_profiles = {
            campaign_id: max(
                items, key=lambda item: (item[1].effective_from, item[0])
            )[1]
            for campaign_id, items in versioned_profiles.items()
        }
        campaign_ids = sorted(set(metrics) | set(snapshots) | set(versions))
        return tuple(
            CampaignReadModel(
                campaign_id,
                snapshots.get(campaign_id),
                metrics.get(campaign_id),
                current_profiles.get(campaign_id),
                versions.get(campaign_id, ()),
            )
            for campaign_id in campaign_ids
        )

    def review_read_models_as_of(
        self, store_id: str, decision_at: datetime
    ) -> tuple[ReviewReadModel, ...]:
        """用一次快照事务批量读取复盘记录及活动+精确动作统计。"""
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        cutoff = _datetime_epoch_us(decision_at.astimezone(UTC))
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    """WITH visible_recommendation AS (
                        SELECT * FROM optimizer_recommendations
                        WHERE store_id=?
                          AND optimizer_utc_epoch_us(generated_at)<=?
                    ), visible_feedback AS (
                        SELECT feedback.*
                        FROM optimizer_recommendation_feedback AS feedback
                        JOIN visible_recommendation AS recommendation
                          ON recommendation.row_id=feedback.recommendation_row_id
                        WHERE feedback.executed_at_us<=?
                    ), ranked_evaluation AS (
                        SELECT evaluation.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY evaluation.recommendation_row_id,
                                                evaluation.feedback_id
                                   ORDER BY evaluation.revision DESC,
                                            evaluation.evaluated_at_us DESC,
                                            evaluation.evaluation_id
                               ) AS latest_rank
                        FROM optimizer_recommendation_evaluations AS evaluation
                        JOIN visible_recommendation AS recommendation
                          ON recommendation.row_id=evaluation.recommendation_row_id
                        WHERE evaluation.evaluated_at_us<=?
                    ), grouped AS (
                        SELECT recommendation.campaign_id,recommendation.action,
                               COUNT(DISTINCT recommendation.row_id) AS total_recommendations,
                               COUNT(DISTINCT CASE
                                   WHEN feedback.feedback_id IS NOT NULL
                                   THEN recommendation.row_id END) AS feedback_count,
                               COUNT(DISTINCT CASE
                                   WHEN feedback.status IN ('executed','modified')
                                   THEN recommendation.row_id END) AS adopted_count,
                               COALESCE(SUM(evaluation.status IN ('effective','no_change','negative')),0) AS completed_count,
                               COALESCE(SUM(evaluation.status='effective'),0) AS effective_count
                        FROM visible_recommendation AS recommendation
                        LEFT JOIN visible_feedback AS feedback
                          ON feedback.recommendation_row_id=recommendation.row_id
                        LEFT JOIN ranked_evaluation AS evaluation
                          ON evaluation.recommendation_row_id=recommendation.row_id
                         AND evaluation.feedback_id=feedback.feedback_id
                         AND evaluation.latest_rank=1
                        GROUP BY recommendation.campaign_id,recommendation.action
                    )
                    SELECT feedback.*,recommendation.recommendation_id,
                           recommendation.revision AS recommendation_revision,
                           recommendation.payload_json AS recommendation_payload,
                           evaluation.payload_json AS evaluation_payload,
                           grouped.total_recommendations,grouped.feedback_count,
                           grouped.adopted_count,grouped.completed_count,
                           grouped.effective_count
                    FROM visible_feedback AS feedback
                    JOIN visible_recommendation AS recommendation
                      ON recommendation.row_id=feedback.recommendation_row_id
                    LEFT JOIN ranked_evaluation AS evaluation
                      ON evaluation.recommendation_row_id=recommendation.row_id
                     AND evaluation.feedback_id=feedback.feedback_id
                     AND evaluation.latest_rank=1
                    JOIN grouped
                      ON grouped.campaign_id=recommendation.campaign_id
                     AND grouped.action=recommendation.action
                    ORDER BY feedback.executed_at_us,feedback.feedback_id""",
                    (store_id, cutoff, cutoff, cutoff),
                ).fetchall()
                evaluations = [
                    None
                    if row["evaluation_payload"] is None
                    else _evaluation_from_payload(row["evaluation_payload"])
                    for row in rows
                ]
                bounded = [item for item in evaluations if item is not None]
                metric_rows = []
                if bounded:
                    metric_rows = connection.execute(
                        """WITH logical AS (
                            SELECT observation.*,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY campaign_id,item_id,data_date,grain
                                       ORDER BY collected_at_us DESC,row_id DESC
                                   ) AS logical_rank
                            FROM optimizer_ad_metric_observations AS observation
                            WHERE store_id=? AND grain='campaign'
                              AND data_date BETWEEN ? AND ?
                              AND collected_at_us<=?
                        )
                        SELECT * FROM logical
                        WHERE logical_rank=1 AND is_deleted=0""",
                        (
                            store_id,
                            min(item.observation_start for item in bounded).isoformat(),
                            max(item.observation_end for item in bounded).isoformat(),
                            cutoff,
                        ),
                    ).fetchall()
            finally:
                connection.rollback()
        daily: dict[tuple[str, date], tuple[Decimal, Decimal]] = {}
        for row in metric_rows:
            key = (row["campaign_id"], date.fromisoformat(row["data_date"]))
            spend, gmv = daily.get(key, (Decimal(0), Decimal(0)))
            daily[key] = (spend + Decimal(row["spend"]), gmv + Decimal(row["gmv"]))
        result = []
        for row, evaluation in zip(rows, evaluations, strict=True):
            recommendation = _recommendation_from_payload(
                row["recommendation_payload"]
            )
            result.append(
                ReviewReadModel(
                    _feedback_from_row(row),
                    recommendation,
                    evaluation,
                    int(row["total_recommendations"]),
                    int(row["feedback_count"]),
                    int(row["adopted_count"]),
                    int(row["completed_count"]),
                    int(row["effective_count"]),
                    _rollback_triggered(recommendation, evaluation, daily),
                )
            )
        return tuple(result)

    def has_successful_collection(self, store_id: str, data_date: date) -> bool:
        _require_business_date(data_date, "data_date")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
                (store_id, data_date.isoformat()),
            ).fetchone()
        return row is not None and row["status"] == "success"

    def save_snapshot(self, snapshot: CampaignSnapshot) -> CampaignSnapshot:
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO optimizer_campaign_snapshots(
                    store_id,campaign_id,campaign_name,campaign_type,status,current_budget,target_roas,currency,observed_at,data_date,observed_at_us
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, campaign_id, observed_at) DO UPDATE SET
                    campaign_name=excluded.campaign_name, campaign_type=excluded.campaign_type,
                    status=excluded.status, current_budget=excluded.current_budget,
                    target_roas=excluded.target_roas, currency=excluded.currency""",
                (
                    snapshot.store_id,
                    snapshot.campaign_id,
                    snapshot.campaign_name,
                    snapshot.campaign_type,
                    snapshot.status.value,
                    _decimal_or_none(snapshot.current_budget),
                    _decimal_or_none(snapshot.target_roas),
                    snapshot.currency,
                    snapshot.observed_at.astimezone(UTC).isoformat(),
                    snapshot.data_date.isoformat(),
                    _datetime_epoch_us(snapshot.observed_at.astimezone(UTC)),
                ),
            )
        return snapshot

    def replace_store_day(
        self,
        store_id: str,
        data_date: date,
        metrics: tuple[AdMetric, ...],
        snapshots: tuple[CampaignSnapshot, ...],
        status: str,
        *,
        note: str = "",
        claim_token: str | None = None,
    ) -> None:
        """原子替换一个店铺业务日的指标并记录采集结果。

        控制面快照以页面观察时间为自然幂等键，因而只 upsert、不删除历史快照；
        日粒度指标则先整体替换，避免重跑遗留已经消失的逻辑行。
        """
        if not isinstance(store_id, str) or not store_id.strip():
            raise ValueError("store_id must not be empty")
        _require_business_date(data_date, "data_date")
        if status != "success":
            raise ValueError("replace_store_day only accepts success")
        if not metrics:
            raise ValueError("success collection requires metrics")
        for metric in metrics:
            if metric.store_id != store_id or metric.data_date != data_date:
                raise ValueError(
                    "metrics must belong to the replacement store and date"
                )
        for snapshot in snapshots:
            if snapshot.store_id != store_id or snapshot.data_date != data_date:
                raise ValueError("snapshots must belong to the replacement store")
        with self.database.transaction() as connection:
            _assert_claim_owner(connection, store_id, data_date, claim_token)
            incoming_keys = {
                (metric.campaign_id, metric.item_id, metric.grain) for metric in metrics
            }
            observation_time = max(
                (metric.collected_at for metric in metrics),
            )
            for metric in metrics:
                connection.execute(
                    _METRIC_OBSERVATION_INSERT_IGNORE,
                    (*_metric_values(metric), 0),
                )
            visible_rows = connection.execute(
                """WITH visible AS (
                    SELECT observation.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY store_id,campaign_id,item_id,data_date,grain
                               ORDER BY collected_at_us DESC, row_id DESC
                           ) AS visible_rank
                    FROM optimizer_ad_metric_observations AS observation
                    WHERE store_id=? AND data_date=?
                      AND collected_at_us<=?
                )
                SELECT * FROM visible
                WHERE visible_rank=1 AND is_deleted=0""",
                (
                    store_id,
                    data_date.isoformat(),
                    _datetime_epoch_us(observation_time.astimezone(UTC)),
                ),
            ).fetchall()
            for row in visible_rows:
                previous = _metric_from_row(row)
                if (
                    previous.campaign_id,
                    previous.item_id,
                    previous.grain,
                ) not in incoming_keys:
                    tombstone = replace(previous, collected_at=observation_time)
                    connection.execute(
                        _METRIC_OBSERVATION_INSERT_IGNORE,
                        (*_metric_values(tombstone), 1),
                    )
            connection.execute(
                "DELETE FROM optimizer_ad_metrics WHERE store_id=? AND data_date=?",
                (store_id, data_date.isoformat()),
            )
            current_rows = connection.execute(
                """WITH current AS (
                    SELECT observation.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY store_id,campaign_id,item_id,data_date,grain
                               ORDER BY collected_at_us DESC, row_id DESC
                           ) AS current_rank
                    FROM optimizer_ad_metric_observations AS observation
                    WHERE store_id=? AND data_date=?
                )
                SELECT * FROM current
                WHERE current_rank=1 AND is_deleted=0
                ORDER BY campaign_id,item_id,grain""",
                (store_id, data_date.isoformat()),
            ).fetchall()
            for row in current_rows:
                metric = _metric_from_row(row)
                connection.execute(
                    """INSERT INTO optimizer_ad_metrics(
                        store_id, campaign_id, campaign_name, item_id, data_date, currency,
                        spend, gmv, orders, collected_at, collected_at_us, source, grain
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, campaign_id, item_id, data_date, grain) DO UPDATE SET
                        campaign_name=excluded.campaign_name, currency=excluded.currency,
                        spend=excluded.spend, gmv=excluded.gmv, orders=excluded.orders,
                        collected_at=excluded.collected_at,
                        collected_at_us=excluded.collected_at_us, source=excluded.source,
                        grain=excluded.grain""",
                    _metric_values(metric),
                )
            for snapshot in snapshots:
                connection.execute(
                    """INSERT INTO optimizer_campaign_snapshots(
                        store_id,campaign_id,campaign_name,campaign_type,status,current_budget,target_roas,currency,observed_at,data_date,observed_at_us
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, campaign_id, observed_at) DO UPDATE SET
                        campaign_name=excluded.campaign_name, campaign_type=excluded.campaign_type,
                        status=excluded.status, current_budget=excluded.current_budget,
                        target_roas=excluded.target_roas, currency=excluded.currency""",
                    (
                        snapshot.store_id,
                        snapshot.campaign_id,
                        snapshot.campaign_name,
                        snapshot.campaign_type,
                        snapshot.status.value,
                        _decimal_or_none(snapshot.current_budget),
                        _decimal_or_none(snapshot.target_roas),
                        snapshot.currency,
                        snapshot.observed_at.astimezone(UTC).isoformat(),
                        snapshot.data_date.isoformat(),
                        _datetime_epoch_us(snapshot.observed_at.astimezone(UTC)),
                    ),
                )
            if (
                connection.execute(
                    "SELECT 1 FROM optimizer_ad_metrics WHERE store_id=? AND data_date=? LIMIT 1",
                    (store_id, data_date.isoformat()),
                ).fetchone()
                is None
            ):
                raise ValueError("success collection requires persisted metrics")
            attempted_at = datetime.now(UTC)
            connection.execute(
                """INSERT INTO optimizer_collection_days(
                    store_id, data_date, status, run_id, note, attempted_at, next_retry_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, data_date) DO UPDATE SET status=excluded.status,
                    run_id=excluded.run_id, note=excluded.note,
                    attempted_at=excluded.attempted_at, next_retry_at=excluded.next_retry_at,
                    claim_token=NULL, claim_until=NULL""",
                (
                    store_id,
                    data_date.isoformat(),
                    status,
                    None,
                    note,
                    attempted_at.isoformat(timespec="microseconds"),
                    None,
                ),
            )
            _append_collection_day_event(
                connection,
                store_id,
                data_date,
                status,
                run_id=None,
                note=note,
                attempted_at=attempted_at,
            )

    def get_snapshot(
        self, store_id: str, campaign_id: str, observed_at: datetime
    ) -> CampaignSnapshot | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM optimizer_campaign_snapshots
                WHERE store_id=? AND campaign_id=? AND observed_at_us=?""",
                (
                    store_id,
                    campaign_id,
                    _datetime_epoch_us(observed_at.astimezone(UTC)),
                ),
            ).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def list_snapshots(
        self, store_id: str, campaign_id: str
    ) -> tuple[CampaignSnapshot, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM optimizer_campaign_snapshots
                WHERE store_id=? AND campaign_id=? ORDER BY observed_at_us""",
                (store_id, campaign_id),
            ).fetchall()
        return tuple(_snapshot_from_row(row) for row in rows)

    def latest_snapshot_for(
        self,
        store_id: str,
        campaign_id: str,
        as_of_data_date: date,
        *,
        decision_at: datetime | None = None,
    ) -> CampaignSnapshot | None:
        _require_business_date(as_of_data_date, "as_of_data_date")
        with self.database.connect() as connection:
            query = """SELECT * FROM optimizer_campaign_snapshots
                WHERE store_id=? AND campaign_id=? AND data_date<=?"""
            values: tuple[object, ...] = (
                store_id,
                campaign_id,
                as_of_data_date.isoformat(),
            )
            if decision_at is not None:
                if decision_at.tzinfo is None or decision_at.utcoffset() is None:
                    raise ValueError("decision_at must include timezone information")
                query += " AND observed_at_us<=?"
                values += (_datetime_epoch_us(decision_at.astimezone(UTC)),)
            row = connection.execute(
                query + " ORDER BY data_date DESC, observed_at_us DESC LIMIT 1", values
            ).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def save_profile(self, profile: CampaignProfile) -> CampaignProfile:
        payload = _json(profile)
        decision_payload = _json(replace(profile, note=""))
        with self.database.transaction() as connection:
            same_version = connection.execute(
                """SELECT payload_json FROM optimizer_campaign_profile_versions
                WHERE store_id=? AND campaign_id=? AND payload_json=? LIMIT 1""",
                (profile.store_id, profile.campaign_id, payload),
            ).fetchone()
            if same_version is not None:
                return _profile_from_payload(same_version["payload_json"])
            current = connection.execute(
                "SELECT version, payload_json FROM optimizer_campaign_profiles WHERE store_id=? AND campaign_id=?",
                (profile.store_id, profile.campaign_id),
            ).fetchone()
            if current is not None and current["payload_json"] == payload:
                return _profile_from_payload(current["payload_json"])
            if current is not None:
                current_profile = _profile_from_payload(current["payload_json"])
                if _json(replace(current_profile, note="")) == decision_payload:
                    return current_profile
            latest_version = connection.execute(
                """SELECT COALESCE(MAX(version), 0) AS version
                FROM optimizer_campaign_profile_versions
                WHERE store_id=? AND campaign_id=?""",
                (profile.store_id, profile.campaign_id),
            ).fetchone()
            version = int(latest_version["version"]) + 1
            connection.execute(
                "INSERT INTO optimizer_campaign_profile_versions VALUES (?, ?, ?, ?, ?)",
                (
                    profile.store_id,
                    profile.campaign_id,
                    version,
                    payload,
                    profile.effective_from.isoformat(),
                ),
            )
            current_profile = None if current is None else current_profile
            if (
                current_profile is None
                or profile.effective_from >= current_profile.effective_from
            ):
                connection.execute(
                    """INSERT INTO optimizer_campaign_profiles VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, campaign_id) DO UPDATE SET
                        version=excluded.version, payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                    (
                        profile.store_id,
                        profile.campaign_id,
                        version,
                        payload,
                        profile.effective_from.isoformat(),
                    ),
                )
        return profile

    def get_current_profile(
        self, store_id: str, campaign_id: str
    ) -> CampaignProfile | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM optimizer_campaign_profiles WHERE store_id=? AND campaign_id=?",
                (store_id, campaign_id),
            ).fetchone()
        return None if row is None else _profile_from_payload(row["payload_json"])

    def list_current_profiles(self, store_id: str) -> tuple[CampaignProfile, ...]:
        """列出一个店铺当前生效的活动策略，不跨店铺读取。"""
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM optimizer_campaign_profiles
                WHERE store_id=? ORDER BY campaign_id""",
                (store_id,),
            ).fetchall()
        return tuple(_profile_from_payload(row["payload_json"]) for row in rows)

    def get_profile_as_of(
        self, store_id: str, campaign_id: str, data_date: date
    ) -> CampaignProfile | None:
        """取得决策业务日生效的不可变活动档案版本。"""
        _require_business_date(data_date, "data_date")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM optimizer_campaign_profile_versions
                WHERE store_id=? AND campaign_id=? AND created_at<=?
                ORDER BY created_at DESC, version DESC LIMIT 1""",
                (store_id, campaign_id, data_date.isoformat()),
            ).fetchone()
        return None if row is None else _profile_from_payload(row["payload_json"])

    def get_profile_version_as_of(
        self, store_id: str, campaign_id: str, data_date: date
    ) -> int | None:
        _require_business_date(data_date, "data_date")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT version FROM optimizer_campaign_profile_versions
                WHERE store_id=? AND campaign_id=? AND created_at<=?
                ORDER BY created_at DESC, version DESC LIMIT 1""",
                (store_id, campaign_id, data_date.isoformat()),
            ).fetchone()
        return None if row is None else int(row["version"])

    def list_profile_versions(
        self, store_id: str, campaign_id: str
    ) -> tuple[CampaignProfile, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM optimizer_campaign_profile_versions WHERE store_id=? AND campaign_id=? ORDER BY version",
                (store_id, campaign_id),
            ).fetchall()
        return tuple(_profile_from_payload(row["payload_json"]) for row in rows)

    def save_recommendation(self, recommendation: Recommendation) -> Recommendation:
        return self.save_recommendations((recommendation,))[0]

    def save_recommendations(
        self, recommendations: tuple[Recommendation, ...]
    ) -> tuple[Recommendation, ...]:
        """在一个事务中保存一批建议，任意失败都会回滚整批。"""
        if not recommendations:
            return ()
        with self.database.transaction() as connection:
            return tuple(
                self._save_recommendation(connection, recommendation)
                for recommendation in recommendations
            )

    def _save_recommendation(
        self, connection: sqlite3.Connection, recommendation: Recommendation
    ) -> Recommendation:
        content_hash = _recommendation_content_hash(recommendation)
        existing = connection.execute(
            """SELECT payload_json FROM optimizer_recommendations
            WHERE store_id=? AND campaign_id=? AND data_date=?
              AND rule_version=? AND slot=? AND input_hash=? AND content_hash=?""",
            (
                recommendation.store_id,
                recommendation.campaign_id,
                recommendation.data_date.isoformat(),
                recommendation.rule_version,
                recommendation.slot.value,
                recommendation.input_hash,
                content_hash,
            ),
        ).fetchone()
        if existing is not None:
            return _recommendation_from_payload(existing["payload_json"])
        rows = connection.execute(
            """SELECT * FROM optimizer_recommendations
            WHERE store_id=? AND campaign_id=? AND data_date=?
              AND rule_version=? AND slot=?
            ORDER BY revision""",
            (
                recommendation.store_id,
                recommendation.campaign_id,
                recommendation.data_date.isoformat(),
                recommendation.rule_version,
                recommendation.slot.value,
            ),
        ).fetchall()
        for row in rows:
            if (
                row["campaign_id"] == recommendation.campaign_id
                and row["data_date"] == recommendation.data_date.isoformat()
                and row["rule_version"] == recommendation.rule_version
                and row["input_hash"] == recommendation.input_hash
                and row["content_hash"] == content_hash
            ):
                return _recommendation_from_payload(row["payload_json"])
        revision = 1 if not rows else int(rows[-1]["revision"]) + 1
        stored = replace(recommendation, revision=revision)
        stored_payload = _json(stored)
        connection.execute(
            """INSERT INTO optimizer_recommendations(
                recommendation_id, store_id, campaign_id, data_date,
                rule_version, action, slot, input_hash, revision, content_hash, payload_json,
                generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stored.recommendation_id,
                stored.store_id,
                stored.campaign_id,
                stored.data_date.isoformat(),
                stored.rule_version,
                stored.action.value,
                stored.slot.value,
                stored.input_hash,
                revision,
                _recommendation_content_hash(stored),
                stored_payload,
                stored.generated_at.isoformat(),
            ),
        )
        return stored

    def list_recommendations(
        self,
        store_id: str,
        campaign_id: str,
        data_date: date,
        *,
        latest_only: bool = False,
    ) -> tuple[Recommendation, ...]:
        _require_business_date(data_date, "data_date")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM optimizer_recommendations WHERE store_id=? AND campaign_id=? AND data_date=? ORDER BY slot, revision",
                (store_id, campaign_id, data_date.isoformat()),
            ).fetchall()
        values = tuple(
            _recommendation_from_payload(row["payload_json"]) for row in rows
        )
        if not latest_only:
            return values
        latest: dict[object, Recommendation] = {}
        for item in values:
            latest[item.slot] = item
        return tuple(latest[key] for key in sorted(latest, key=lambda slot: slot.value))

    def latest_recommendations_for_store_day_as_of(
        self, store_id: str, data_date: date, *, generated_by: datetime
    ) -> tuple[Recommendation, ...]:
        """按活动和建议槽返回截止时点前的最新不可变建议。"""
        _require_business_date(data_date, "data_date")
        if generated_by.tzinfo is None or generated_by.utcoffset() is None:
            raise ValueError("generated_by must include timezone information")
        with self.database.connect() as connection:
            return _latest_recommendations_for_store_day_as_of_connection(
                connection, store_id, data_date, generated_by.astimezone(UTC)
            )

    def get_recommendation(
        self, store_id: str, recommendation_id: str, revision: int
    ) -> Recommendation | None:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise ValueError("revision must be a positive integer")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM optimizer_recommendations
                WHERE store_id=? AND recommendation_id=? AND revision=?""",
                (store_id, recommendation_id, revision),
            ).fetchone()
        return (
            None if row is None else _recommendation_from_payload(row["payload_json"])
        )

    def save_feedback(self, store_id: str, feedback: Feedback) -> Feedback:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT feedback.*, recommendation.recommendation_id,
                          recommendation.revision AS recommendation_revision
                FROM optimizer_recommendation_feedback AS feedback
                JOIN optimizer_recommendations AS recommendation
                  ON recommendation.row_id=feedback.recommendation_row_id
                WHERE feedback.feedback_id=? AND recommendation.store_id=?""",
                (feedback.feedback_id, store_id),
            ).fetchone()
            if row is not None:
                existing = _feedback_from_row(row)
                if existing != feedback:
                    raise ValueError(
                        "feedback conflict for immutable recommendation revision"
                    )
                return existing
            if (
                connection.execute(
                    "SELECT 1 FROM optimizer_recommendation_feedback WHERE feedback_id=?",
                    (feedback.feedback_id,),
                ).fetchone()
                is not None
            ):
                raise ValueError("feedback_id already belongs to another store")
            recommendation_row_id = _recommendation_row_id(
                connection,
                store_id,
                feedback.recommendation_id,
                feedback.recommendation_revision,
            )
            connection.execute(
                """INSERT INTO optimizer_recommendation_feedback(
                    feedback_id, recommendation_row_id, status, actual_value,
                    executed_at, note, executed_at_us
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    feedback.feedback_id,
                    recommendation_row_id,
                    feedback.status.value,
                    _decimal_or_none(feedback.actual_value),
                    feedback.executed_at.astimezone(UTC).isoformat(
                        timespec="microseconds"
                    ),
                    feedback.note,
                    _datetime_epoch_us(feedback.executed_at.astimezone(UTC)),
                ),
            )
        return feedback

    def list_feedback(
        self, store_id: str, recommendation_id: str
    ) -> tuple[Feedback, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT feedback.*, recommendation.recommendation_id
                    , recommendation.revision AS recommendation_revision
                FROM optimizer_recommendation_feedback AS feedback
                JOIN optimizer_recommendations AS recommendation
                  ON recommendation.row_id=feedback.recommendation_row_id
                WHERE recommendation.store_id=? AND recommendation.recommendation_id=?
                ORDER BY feedback.executed_at_us, feedback.feedback_id""",
                (store_id, recommendation_id),
            ).fetchall()
        return tuple(_feedback_from_row(row) for row in rows)

    def get_feedback(self, store_id: str, feedback_id: str) -> Feedback | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT feedback.*, recommendation.recommendation_id,
                          recommendation.revision AS recommendation_revision
                FROM optimizer_recommendation_feedback AS feedback
                JOIN optimizer_recommendations AS recommendation
                  ON recommendation.row_id=feedback.recommendation_row_id
                WHERE recommendation.store_id=? AND feedback.feedback_id=?""",
                (store_id, feedback_id),
            ).fetchone()
        return None if row is None else _feedback_from_row(row)

    def list_feedback_for_store(self, store_id: str) -> tuple[Feedback, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT feedback.*, recommendation.recommendation_id,
                          recommendation.revision AS recommendation_revision
                FROM optimizer_recommendation_feedback AS feedback
                JOIN optimizer_recommendations AS recommendation
                  ON recommendation.row_id=feedback.recommendation_row_id
                WHERE recommendation.store_id=?
                ORDER BY feedback.executed_at_us, feedback.feedback_id""",
                (store_id,),
            ).fetchall()
        return tuple(_feedback_from_row(row) for row in rows)

    def pending_feedback_for_store(
        self, store_id: str, *, as_of: datetime | None = None
    ) -> tuple[Feedback, ...]:
        """一次集合查询返回每条反馈最新评估仍未完成的人工执行。"""
        with self.database.connect() as connection:
            if as_of is None:
                rows = connection.execute(_PENDING_FEEDBACK_SQL, (store_id,)).fetchall()
            else:
                if as_of.tzinfo is None or as_of.utcoffset() is None:
                    raise ValueError("as_of must include timezone information")
                return _pending_feedback_as_of_connection(
                    connection,
                    store_id,
                    _datetime_epoch_us(as_of.astimezone(UTC)),
                )
        return tuple(_feedback_from_row(row) for row in rows)

    def save_evaluation(self, store_id: str, evaluation: Evaluation) -> Evaluation:
        with self.database.transaction() as connection:
            recommendation_row_id = _recommendation_row_id(
                connection,
                store_id,
                evaluation.recommendation_id,
                evaluation.recommendation_revision,
            )
            id_owner = connection.execute(
                """SELECT recommendation.store_id
                FROM optimizer_recommendation_evaluations AS evaluation
                JOIN optimizer_recommendations AS recommendation
                  ON recommendation.row_id=evaluation.recommendation_row_id
                WHERE evaluation.evaluation_id=?""",
                (evaluation.evaluation_id,),
            ).fetchone()
            if id_owner is not None and id_owner["store_id"] != store_id:
                raise ValueError("evaluation_id already belongs to another store")
            feedback_row = connection.execute(
                """SELECT status, actual_value FROM optimizer_recommendation_feedback
                WHERE feedback_id=? AND recommendation_row_id=?""",
                (evaluation.feedback_id, recommendation_row_id),
            ).fetchone()
            if feedback_row is None or feedback_row["status"] not in {
                FeedbackStatus.EXECUTED.value,
                FeedbackStatus.MODIFIED.value,
            }:
                raise ValueError(
                    "evaluation requires executed feedback on exact recommendation revision"
                )
            recommendation_row = connection.execute(
                "SELECT payload_json FROM optimizer_recommendations WHERE row_id=?",
                (recommendation_row_id,),
            ).fetchone()
            recommendation = _recommendation_from_payload(
                recommendation_row["payload_json"]
            )
            expected_applied = (
                _decimal(feedback_row["actual_value"])
                if feedback_row["status"] == FeedbackStatus.MODIFIED.value
                else recommendation.proposed_value
            )
            if evaluation.applied_value != expected_applied:
                raise ValueError("evaluation applied_value does not match feedback")
            same_input = connection.execute(
                """SELECT payload_json
                FROM optimizer_recommendation_evaluations
                WHERE recommendation_row_id=? AND feedback_id=? AND input_hash=?""",
                (
                    recommendation_row_id,
                    evaluation.feedback_id,
                    evaluation.input_hash,
                ),
            ).fetchone()
            if same_input is not None:
                return _evaluation_from_payload(same_input["payload_json"])
            row = connection.execute(
                """SELECT payload_json
                FROM optimizer_recommendation_evaluations
                WHERE evaluation_id=?""",
                (evaluation.evaluation_id,),
            ).fetchone()
            if row is not None:
                raise ValueError("evaluation conflict for immutable evaluation_id")
            revision_row = connection.execute(
                """SELECT COALESCE(MAX(revision), 0) AS latest_revision
                FROM optimizer_recommendation_evaluations
                WHERE recommendation_row_id=? AND feedback_id=?""",
                (recommendation_row_id, evaluation.feedback_id),
            ).fetchone()
            saved = replace(
                evaluation, revision=int(revision_row["latest_revision"]) + 1
            )
            payload = _json(saved)
            connection.execute(
                """INSERT INTO optimizer_recommendation_evaluations(
                    evaluation_id, recommendation_row_id, status, baseline_start,
                    baseline_end, observation_start, observation_end,
                    algorithm_version, evaluated_at, payload_json, note,
                    feedback_id, input_hash, revision, evaluated_at_us
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    saved.evaluation_id,
                    recommendation_row_id,
                    saved.status.value,
                    saved.baseline_start.isoformat(),
                    saved.baseline_end.isoformat(),
                    saved.observation_start.isoformat(),
                    saved.observation_end.isoformat(),
                    saved.algorithm_version,
                    saved.evaluated_at.astimezone(UTC).isoformat(
                        timespec="microseconds"
                    ),
                    payload,
                    saved.note,
                    saved.feedback_id,
                    saved.input_hash,
                    saved.revision,
                    _datetime_epoch_us(saved.evaluated_at.astimezone(UTC)),
                ),
            )
        return saved

    def list_evaluations(
        self, store_id: str, recommendation_id: str
    ) -> tuple[Evaluation, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT evaluation.payload_json
                FROM optimizer_recommendation_evaluations AS evaluation
                JOIN optimizer_recommendations AS recommendation
                  ON recommendation.row_id=evaluation.recommendation_row_id
                WHERE recommendation.store_id=? AND recommendation.recommendation_id=?
                ORDER BY evaluation.evaluated_at_us, evaluation.evaluation_id""",
                (store_id, recommendation_id),
            ).fetchall()
        return tuple(_evaluation_from_payload(row["payload_json"]) for row in rows)

    def evaluation_records(
        self,
        store_id: str,
        *,
        campaign_id: str | None = None,
        action: Action | None = None,
        evaluated_through: date | None = None,
    ) -> tuple[EvaluationRecord, ...]:
        """读取效果统计所需的不可变建议与评估配对。"""
        clauses = ["recommendation.store_id=?"]
        values: list[object] = [store_id]
        if campaign_id is not None:
            clauses.append("recommendation.campaign_id=?")
            values.append(campaign_id)
        if action is not None:
            clauses.append("recommendation.action=?")
            values.append(action.value)
        if evaluated_through is not None:
            _require_business_date(evaluated_through, "evaluated_through")
            clauses.append("substr(evaluation.evaluated_at, 1, 10)<=?")
            values.append(evaluated_through.isoformat())
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT recommendation.payload_json AS recommendation_payload,
                          evaluation.payload_json AS evaluation_payload
                FROM optimizer_recommendation_evaluations AS evaluation
                JOIN optimizer_recommendations AS recommendation
                  ON recommendation.row_id=evaluation.recommendation_row_id
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY evaluation.evaluated_at, evaluation.evaluation_id",
                tuple(values),
            ).fetchall()
        return tuple(
            EvaluationRecord(
                _recommendation_from_payload(row["recommendation_payload"]),
                _evaluation_from_payload(row["evaluation_payload"]),
            )
            for row in rows
        )

    def latest_completed_evaluation_records(
        self,
        store_id: str,
        action: Action,
        evaluated_before: datetime,
        *,
        campaign_id: str | None = None,
    ) -> tuple[EvaluationRecord, ...]:
        """在 SQL 层筛选精确动作及截止时点，并只返回每反馈最新完成修订。"""
        if not isinstance(action, Action):
            raise TypeError("action must be an Action")
        if evaluated_before.tzinfo is None or evaluated_before.utcoffset() is None:
            raise ValueError("evaluated_before must include timezone information")
        campaign_clause = ""
        values: list[object] = [
            store_id,
            action.value,
            _datetime_epoch_us(evaluated_before.astimezone(UTC)),
        ]
        if campaign_id is not None:
            campaign_clause = " AND recommendation.campaign_id=?"
            values.append(campaign_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """WITH relevant AS (
                    SELECT recommendation.payload_json AS recommendation_payload,
                           evaluation.payload_json AS evaluation_payload,
                           evaluation.status,
                           ROW_NUMBER() OVER (
                               PARTITION BY evaluation.recommendation_row_id,
                                            evaluation.feedback_id
                               ORDER BY evaluation.revision DESC
                           ) AS latest_rank
                    FROM optimizer_recommendation_evaluations AS evaluation
                    JOIN optimizer_recommendations AS recommendation
                      ON recommendation.row_id=evaluation.recommendation_row_id
                    WHERE recommendation.store_id=?
                      AND recommendation.action=?
                      AND evaluation.evaluated_at_us<?"""
                + campaign_clause
                + """
                )
                SELECT recommendation_payload,evaluation_payload
                FROM relevant
                WHERE latest_rank=1
                  AND status IN ('effective','no_change','negative')""",
                tuple(values),
            ).fetchall()
        return tuple(
            EvaluationRecord(
                _recommendation_from_payload(row["recommendation_payload"]),
                _evaluation_from_payload(row["evaluation_payload"]),
            )
            for row in rows
        )

    def save_run_log(self, run_log: RunLog) -> RunLog:
        _validate_collection_status(run_log.status)
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO optimizer_run_logs VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,
                    finished_at=excluded.finished_at, details_json=excluded.details_json""",
                (
                    run_log.run_id,
                    run_log.status,
                    run_log.started_at.isoformat(),
                    None
                    if run_log.finished_at is None
                    else run_log.finished_at.isoformat(),
                    _json_dump(run_log.details or {}),
                ),
            )
        return run_log

    def get_run_log(self, run_id: str) -> RunLog | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM optimizer_run_logs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return RunLog(
            row["run_id"],
            row["status"],
            datetime.fromisoformat(row["started_at"]),
            None
            if row["finished_at"] is None
            else datetime.fromisoformat(row["finished_at"]),
            _json_load(row["details_json"]),
        )

    def get_collection_day(
        self, store_id: str, data_date: date
    ) -> CollectionDay | None:
        _require_business_date(data_date, "data_date")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT store_id, data_date, status, attempted_at, next_retry_at, claim_token, claim_until "
                "FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
                (store_id, data_date.isoformat()),
            ).fetchone()
        return None if row is None else _collection_day_from_row(row)

    def get_collection_day_as_of(
        self, store_id: str, data_date: date, decision_at: datetime
    ) -> CollectionDay | None:
        """回放截止决策时点已成为当前结果的最新采集状态。"""
        _require_business_date(data_date, "data_date")
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must include timezone information")
        with self.database.connect() as connection:
            return _collection_day_as_of_connection(
                connection,
                store_id,
                data_date,
                _datetime_epoch_us(decision_at.astimezone(UTC)),
            )

    def list_collection_days(
        self, store_id: str, start: date, end: date
    ) -> tuple[CollectionDay, ...]:
        _require_business_date(start, "start")
        _require_business_date(end, "end")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT store_id, data_date, status, attempted_at, next_retry_at, claim_token, claim_until "
                "FROM optimizer_collection_days WHERE store_id=? AND data_date BETWEEN ? AND ? "
                "ORDER BY data_date",
                (store_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        return tuple(_collection_day_from_row(row) for row in rows)

    def claim_collection_day(
        self,
        store_id: str,
        data_date: date,
        claim_token: str,
        claim_until: datetime,
    ) -> bool:
        """原子领取一个业务日；success 或未过期 claim 不可抢。"""
        _require_business_date(data_date, "data_date")
        if not isinstance(claim_token, str) or not claim_token.strip():
            raise ValueError("claim_token must not be empty")
        if claim_until.tzinfo is None or claim_until.utcoffset() is None:
            raise ValueError("claim_until must include timezone information")
        now = datetime.now(UTC)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT status, claim_token, claim_until FROM optimizer_collection_days "
                "WHERE store_id=? AND data_date=?",
                (store_id, data_date.isoformat()),
            ).fetchone()
            if row is not None and row["status"] == "success":
                return False
            if row is not None and row["claim_until"] is not None:
                current_claim_until = datetime.fromisoformat(row["claim_until"])
                if current_claim_until > now and row["claim_token"] != claim_token:
                    return False
            if row is None:
                connection.execute(
                    "INSERT INTO optimizer_collection_days("
                    "store_id,data_date,status,note,attempted_at,claim_token,claim_until) "
                    "VALUES (?, ?, 'failed', ?, ?, ?, ?)",
                    (
                        store_id,
                        data_date.isoformat(),
                        COLLECTION_CLAIM_SENTINEL,
                        now.isoformat(timespec="microseconds"),
                        claim_token,
                        claim_until.astimezone(UTC).isoformat(timespec="microseconds"),
                    ),
                )
            else:
                connection.execute(
                    "UPDATE optimizer_collection_days SET claim_token=?, claim_until=? "
                    "WHERE store_id=? AND data_date=?",
                    (
                        claim_token,
                        claim_until.astimezone(UTC).isoformat(timespec="microseconds"),
                        store_id,
                        data_date.isoformat(),
                    ),
                )
            return True

    def release_collection_claim(
        self, store_id: str, data_date: date, claim_token: str
    ) -> None:
        _require_business_date(data_date, "data_date")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE optimizer_collection_days SET claim_token=NULL, claim_until=NULL "
                "WHERE store_id=? AND data_date=? AND claim_token=?",
                (store_id, data_date.isoformat(), claim_token),
            )

    def record_collection_day(
        self,
        store_id: str,
        data_date: date,
        status: str,
        *,
        run_id: str | None = None,
        note: str = "",
        attempted_at: datetime | None = None,
        next_retry_at: datetime | None = None,
        claim_token: str | None = None,
    ) -> None:
        _require_business_date(data_date, "data_date")
        _validate_collection_status(status)
        _validate_collection_note(note)
        attempted_at = attempted_at or datetime.now(UTC)
        for value, field in (
            (attempted_at, "attempted_at"),
            (next_retry_at, "next_retry_at"),
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{field} must include timezone information")
        with self.database.transaction() as connection:
            _assert_claim_owner(connection, store_id, data_date, claim_token)
            existing = connection.execute(
                "SELECT status FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
                (store_id, data_date.isoformat()),
            ).fetchone()
            if (
                existing is not None
                and existing["status"] == "success"
                and status != "success"
            ):
                return
            if (
                status == "success"
                and connection.execute(
                    "SELECT 1 FROM optimizer_ad_metrics WHERE store_id=? AND data_date=? LIMIT 1",
                    (store_id, data_date.isoformat()),
                ).fetchone()
                is None
            ):
                raise ValueError("success collection requires persisted metrics")
            connection.execute(
                """INSERT INTO optimizer_collection_days(
                    store_id, data_date, status, run_id, note, attempted_at, next_retry_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, data_date) DO UPDATE SET status=excluded.status,
                    run_id=excluded.run_id, note=excluded.note,
                    attempted_at=excluded.attempted_at, next_retry_at=excluded.next_retry_at,
                    claim_token=NULL, claim_until=NULL""",
                (
                    store_id,
                    data_date.isoformat(),
                    status,
                    run_id,
                    note,
                    attempted_at.astimezone(UTC).isoformat(timespec="microseconds"),
                    None
                    if next_retry_at is None
                    else next_retry_at.astimezone(UTC).isoformat(
                        timespec="microseconds"
                    ),
                ),
            )
            _append_collection_day_event(
                connection,
                store_id,
                data_date,
                status,
                run_id=run_id,
                note=note,
                attempted_at=attempted_at,
            )

    def record_collection_failure(
        self,
        store_id: str,
        data_date: date,
        *,
        note: str = "",
        attempted_at: datetime | None = None,
        claim_token: str | None = None,
    ) -> None:
        """记录失败，但不把已验证成功的业务日降级为失败。"""
        _require_business_date(data_date, "data_date")
        _validate_collection_note(note)
        attempted_at = attempted_at or datetime.now(UTC)
        if attempted_at.tzinfo is None or attempted_at.utcoffset() is None:
            raise ValueError("attempted_at must include timezone information")
        with self.database.transaction() as connection:
            _assert_claim_owner(connection, store_id, data_date, claim_token)
            existing = connection.execute(
                "SELECT status FROM optimizer_collection_days WHERE store_id=? AND data_date=?",
                (store_id, data_date.isoformat()),
            ).fetchone()
            if existing is not None and existing["status"] == "success":
                return
            connection.execute(
                """INSERT INTO optimizer_collection_days(
                    store_id, data_date, status, run_id, note, attempted_at, next_retry_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, data_date) DO UPDATE SET status=excluded.status,
                    run_id=excluded.run_id, note=excluded.note,
                    attempted_at=excluded.attempted_at, next_retry_at=excluded.next_retry_at,
                    claim_token=NULL, claim_until=NULL""",
                (
                    store_id,
                    data_date.isoformat(),
                    "failed",
                    None,
                    note,
                    attempted_at.astimezone(UTC).isoformat(timespec="microseconds"),
                    None,
                ),
            )
            _append_collection_day_event(
                connection,
                store_id,
                data_date,
                "failed",
                run_id=None,
                note=note,
                attempted_at=attempted_at,
            )

    def collection_gaps(
        self, store_id: str, start: date, end: date
    ) -> tuple[date, ...]:
        _require_business_date(start, "start")
        _require_business_date(end, "end")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT data_date, status FROM optimizer_collection_days WHERE store_id=? AND data_date BETWEEN ? AND ?",
                (store_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        present = {
            date.fromisoformat(row["data_date"])
            for row in rows
            if row["status"] == "success"
        }
        days: list[date] = []
        current = start
        while current <= end:
            if current not in present:
                days.append(current)
            current = date.fromordinal(current.toordinal() + 1)
        return tuple(days)


def _json(value: object) -> str:
    return json.dumps(
        asdict(value),
        default=_domain_json_default,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_dump(value: object) -> str:
    return json.dumps(
        _prepare_json(value),
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
    )


def _prepare_json(value: object) -> object:
    """在 JSON 编码器看到 str Enum 前保留其具体枚举类型。"""
    if isinstance(value, Enum):
        return {
            "__optimizer_type__": "enum",
            "enum": type(value).__name__,
            "value": value.value,
        }
    if isinstance(value, dict):
        return {str(key): _prepare_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare_json(item) for item in value]
    if isinstance(value, tuple):
        return [_prepare_json(item) for item in value]
    return value


def _json_load(value: str) -> object:
    return json.loads(value, object_hook=_json_object_hook)


def _recommendation_content_hash(recommendation: Recommendation) -> str:
    """同一规则输入的重试不因传输 ID、修订号或生成时间而新建历史。"""
    value = asdict(recommendation)
    for key in ("recommendation_id", "revision", "generated_at"):
        value.pop(key)
    for evidence in value.get("evidence", []):
        quality = evidence.get("quality")
        if isinstance(quality, dict):
            quality.pop("assessed_at", None)
    canonical = json.dumps(
        value, default=_domain_json_default, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _domain_json_default(value: object) -> str | list[str]:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (frozenset, tuple)):
        return sorted(
            item.value if isinstance(item, Enum) else str(item) for item in value
        )
    raise TypeError(f"cannot serialize {type(value)!r}")


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return {"__optimizer_type__": "Decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"__optimizer_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__optimizer_type__": "date", "value": value.isoformat()}
    if isinstance(value, Enum):
        return {
            "__optimizer_type__": "enum",
            "enum": type(value).__name__,
            "value": value.value,
        }
    if isinstance(value, (frozenset, tuple)):
        return sorted(
            item.value if isinstance(item, Enum) else str(item) for item in value
        )
    raise TypeError(f"cannot serialize {type(value)!r}")


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _metric_from_row(row: object) -> AdMetric:
    return AdMetric(
        row["store_id"],
        row["campaign_id"],
        row["campaign_name"],
        row["item_id"],
        date.fromisoformat(row["data_date"]),
        row["currency"],
        Decimal(row["spend"]),
        Decimal(row["gmv"]),
        int(row["orders"]),
        datetime.fromisoformat(row["collected_at"]),
        row["source"],
        MetricGrain(row["grain"]),
    )


def _snapshot_from_row(row: object) -> CampaignSnapshot:
    return CampaignSnapshot(
        row["store_id"],
        row["campaign_id"],
        row["campaign_name"],
        row["campaign_type"],
        CampaignStatus(row["status"]),
        _decimal(row["current_budget"]),
        _decimal(row["target_roas"]),
        row["currency"],
        datetime.fromisoformat(row["observed_at"]),
        date.fromisoformat(row["data_date"]),
    )


def _collection_day_as_of_connection(
    connection: object, store_id: str, data_date: date, cutoff: int
) -> CollectionDay | None:
    row = connection.execute(
        """SELECT store_id,data_date,status,attempted_at,
                  NULL AS next_retry_at,NULL AS claim_token,NULL AS claim_until
        FROM optimizer_collection_day_events
        WHERE store_id=? AND data_date=? AND attempted_at_us<=?
        ORDER BY attempted_at_us DESC,row_id DESC
        LIMIT 1""",
        (store_id, data_date.isoformat(), cutoff),
    ).fetchone()
    return None if row is None else _collection_day_from_row(row)


def _latest_metric_date_as_of_connection(
    connection: object, store_id: str, data_through: date, cutoff: int
) -> date | None:
    row = connection.execute(
        _LATEST_METRIC_DATE_AS_OF_SQL,
        (store_id, data_through.isoformat(), cutoff, cutoff),
    ).fetchone()
    return None if row is None else date.fromisoformat(row["data_date"])


def _campaign_metrics_for_store_day_as_of_connection(
    connection: object, store_id: str, data_date: date, cutoff: int
) -> tuple[AdMetric, ...]:
    rows = connection.execute(
        """WITH visible AS (
            SELECT observation.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY store_id,campaign_id,item_id,data_date,grain
                       ORDER BY collected_at_us DESC,row_id DESC
                   ) AS visible_rank
            FROM optimizer_ad_metric_observations AS observation
            WHERE store_id=? AND data_date=? AND collected_at_us<=?
        )
        SELECT * FROM visible
        WHERE visible_rank=1 AND is_deleted=0 AND grain='campaign'
        ORDER BY campaign_id,item_id""",
        (store_id, data_date.isoformat(), cutoff),
    ).fetchall()
    return tuple(_metric_from_row(row) for row in rows)


def _latest_recommendations_for_store_day_as_of_connection(
    connection: object, store_id: str, data_date: date, cutoff: datetime
) -> tuple[Recommendation, ...]:
    rows = connection.execute(
        """SELECT payload_json FROM optimizer_recommendations
        WHERE store_id=? AND data_date=?""",
        (store_id, data_date.isoformat()),
    ).fetchall()
    latest: dict[tuple[str, RecommendationSlot], Recommendation] = {}
    for row in rows:
        item = _recommendation_from_payload(row["payload_json"])
        if item.generated_at.astimezone(UTC) > cutoff:
            continue
        key = (item.campaign_id, item.slot)
        current = latest.get(key)
        if current is None or (
            item.generated_at.astimezone(UTC),
            item.revision,
            item.recommendation_id,
        ) > (
            current.generated_at.astimezone(UTC),
            current.revision,
            current.recommendation_id,
        ):
            latest[key] = item
    return tuple(
        latest[key]
        for key in sorted(latest, key=lambda value: (value[0], value[1].value))
    )


def _pending_feedback_as_of_connection(
    connection: object, store_id: str, cutoff: int
) -> tuple[Feedback, ...]:
    sql = _PENDING_FEEDBACK_SQL.replace(
        "AND candidate.feedback_id=feedback.feedback_id",
        "AND candidate.feedback_id=feedback.feedback_id\n"
        "        AND candidate.evaluated_at_us<=?",
    ).replace(
        "AND feedback.status IN ('executed','modified')",
        "AND feedback.status IN ('executed','modified')\n"
        "  AND feedback.executed_at_us<=?",
    )
    rows = connection.execute(sql, (cutoff, store_id, cutoff)).fetchall()
    return tuple(_feedback_from_row(row) for row in rows)


def _collection_day_from_row(row: object) -> CollectionDay:
    return CollectionDay(
        store_id=row["store_id"],
        data_date=date.fromisoformat(row["data_date"]),
        status=row["status"],
        attempted_at=(
            None
            if row["attempted_at"] is None
            else datetime.fromisoformat(row["attempted_at"])
        ),
        next_retry_at=(
            None
            if row["next_retry_at"] is None
            else datetime.fromisoformat(row["next_retry_at"])
        ),
        claim_token=row["claim_token"],
        claim_until=(
            None
            if row["claim_until"] is None
            else datetime.fromisoformat(row["claim_until"])
        ),
    )


def _profile_from_payload(payload: str) -> CampaignProfile:
    value = json.loads(payload)
    return CampaignProfile(
        value["store_id"],
        value["campaign_id"],
        ProfileStatus(value["status"]),
        date.fromisoformat(value["effective_from"]),
        _decimal(value["minimum_roas"]),
        Decimal(value["minimum_gmv_growth"]),
        Decimal(value["growth_intensity"]),
        Decimal(value["max_budget_step"]),
        Decimal(value["max_target_roas_step"]),
        int(value["min_orders"]),
        int(value["cooldown_days"]),
        int(value["evaluation_days"]),
        int(value["rollback_days"]),
        frozenset(Action(item) for item in value["allowed_actions"]),
        value["note"],
    )


def _recommendation_from_payload(payload: str) -> Recommendation:
    value = json.loads(payload)
    evidence = tuple(_evidence(item) for item in value["evidence"])
    return Recommendation(
        value["recommendation_id"],
        value["store_id"],
        value["campaign_id"],
        date.fromisoformat(value["data_date"]),
        Action(value["action"]),
        Confidence(value["confidence"]),
        int(value["priority"]),
        _decimal(value["current_value"]),
        _decimal(value["proposed_value"]),
        _decimal(value["change_ratio"]),
        evidence,
        _success(value["success_condition"]),
        _rollback(value["rollback_condition"]),
        Decimal(value["expected_impact"]),
        Decimal(value["risk"]),
        int(value["observation_days"]),
        value["rule_version"],
        value["input_hash"],
        int(value["revision"]),
        datetime.fromisoformat(value["generated_at"]),
        RecommendationSlot(value.get("slot", "primary")),
    )


def _evidence(value: dict[str, object]) -> Evidence:
    return Evidence(
        value["code"],
        value["summary"],
        _window(value["short_window"]),
        _window(value["long_window"]),
        _quality(value["quality"]),
        tuple(tuple(item) for item in value["rule_inputs"]),
    )


def _quality(value: dict[str, object]) -> DataQuality:
    return DataQuality(
        DataQualityStatus(value["status"]),
        datetime.fromisoformat(value["assessed_at"]),
        bool(value["allow_aggressive_scale"]),
        tuple(value["missing_fields"]),
        value["note"],
    )


def _window(value: dict[str, object]) -> WindowStats:
    return WindowStats(
        date.fromisoformat(value["start_date"]),
        date.fromisoformat(value["end_date"]),
        Decimal(value["spend"]),
        Decimal(value["gmv"]),
        int(value["orders"]),
    )


def _success(value: dict[str, object]) -> SuccessCondition:
    return SuccessCondition(
        _decimal(value["minimum_roas"]),
        Decimal(value["minimum_gmv_growth"]),
        int(value["observation_days"]),
    )


def _rollback(value: dict[str, object]) -> RollbackCondition:
    return RollbackCondition(
        _decimal(value["roas_below"]), int(value["consecutive_days"])
    )


def _feedback_from_row(row: object) -> Feedback:
    return Feedback(
        row["feedback_id"],
        row["recommendation_id"],
        FeedbackStatus(row["status"]),
        _decimal(row["actual_value"]),
        datetime.fromisoformat(row["executed_at"]),
        row["note"],
        int(row["recommendation_revision"]),
    )


def _recommendation_row_id(
    connection: object, store_id: str, recommendation_id: str, revision: int
) -> int:
    row = connection.execute(
        """SELECT row_id FROM optimizer_recommendations
        WHERE store_id=? AND recommendation_id=? AND revision=?""",
        (store_id, recommendation_id, revision),
    ).fetchone()
    if row is None:
        raise ValueError("recommendation_id does not exist")
    return int(row["row_id"])


def _validate_collection_status(status: str) -> None:
    if status not in {"success", "failed", "no_data"}:
        raise ValueError("collection status must be success, failed, or no_data")


def _validate_collection_note(note: str) -> None:
    if not isinstance(note, str):
        raise TypeError("collection note must be a string")
    if note in {LEGACY_COLLECTION_CLAIM_NOTE, COLLECTION_CLAIM_SENTINEL}:
        raise ValueError("collection note is reserved for internal claim state")


def _rollback_triggered(
    recommendation: Recommendation,
    evaluation: Evaluation | None,
    daily: dict[tuple[str, date], tuple[Decimal, Decimal]],
) -> bool | None:
    if evaluation is None or evaluation.status in {
        EvaluationStatus.PENDING,
        EvaluationStatus.INSUFFICIENT_DATA,
    }:
        return None
    threshold = recommendation.rollback_condition.roas_below
    if threshold is None:
        return False
    streak = 0
    missing = False
    current = evaluation.observation_start
    while current <= evaluation.observation_end:
        values = daily.get((recommendation.campaign_id, current))
        if values is None or values[0] == 0:
            missing = True
            streak = 0
        elif values[1] / values[0] < threshold:
            streak += 1
            if streak >= recommendation.rollback_condition.consecutive_days:
                return True
        else:
            streak = 0
        current = date.fromordinal(current.toordinal() + 1)
    return None if missing else False


def _append_collection_day_event(
    connection: object,
    store_id: str,
    data_date: date,
    status: str,
    *,
    run_id: str | None,
    note: str,
    attempted_at: datetime,
) -> None:
    utc_attempted_at = attempted_at.astimezone(UTC)
    connection.execute(
        """INSERT OR IGNORE INTO optimizer_collection_day_events(
            store_id,data_date,status,run_id,note,attempted_at,attempted_at_us
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            store_id,
            data_date.isoformat(),
            status,
            run_id,
            note,
            utc_attempted_at.isoformat(timespec="microseconds"),
            _datetime_epoch_us(utc_attempted_at),
        ),
    )


def _require_business_date(value: object, field: str) -> None:
    if type(value) is not date:
        raise TypeError(f"{field} must be a date")


def _assert_claim_owner(
    connection: object, store_id: str, data_date: date, claim_token: str | None
) -> None:
    if claim_token is None:
        return
    row = connection.execute(
        "SELECT claim_token, claim_until FROM optimizer_collection_days "
        "WHERE store_id=? AND data_date=?",
        (store_id, data_date.isoformat()),
    ).fetchone()
    if row is None or row["claim_token"] != claim_token or row["claim_until"] is None:
        raise ClaimLost("collection claim is no longer owned")
    if datetime.fromisoformat(row["claim_until"]) <= datetime.now(UTC):
        raise ClaimLost("collection claim has expired")


def _json_object_hook(value: dict[str, object]) -> object:
    marker = value.get("__optimizer_type__")
    if marker == "Decimal":
        return Decimal(str(value["value"]))
    if marker == "datetime":
        return datetime.fromisoformat(str(value["value"]))
    if marker == "date":
        return date.fromisoformat(str(value["value"]))
    if marker == "enum":
        enum_type = {
            "Action": Action,
            "Confidence": Confidence,
            "FeedbackStatus": FeedbackStatus,
            "EvaluationStatus": EvaluationStatus,
            "DataQualityStatus": DataQualityStatus,
            "ProfileStatus": ProfileStatus,
            "CampaignStatus": CampaignStatus,
        }.get(str(value["enum"]))
        return enum_type(str(value["value"])) if enum_type else value
    return value


def _evaluation_from_payload(payload: str) -> Evaluation:
    value = json.loads(payload)
    return Evaluation(
        value["evaluation_id"],
        value["recommendation_id"],
        EvaluationStatus(value["status"]),
        date.fromisoformat(value["baseline_start"]),
        date.fromisoformat(value["baseline_end"]),
        date.fromisoformat(value["observation_start"]),
        date.fromisoformat(value["observation_end"]),
        value["algorithm_version"],
        datetime.fromisoformat(value["evaluated_at"]),
        _window(value["baseline_stats"]),
        _window(value["after_stats"]),
        value["note"],
        int(value.get("recommendation_revision", 1)),
        value.get("feedback_id", "legacy-feedback"),
        _decimal(value.get("applied_value")),
        tuple(date.fromisoformat(item) for item in value.get("input_dates", [])),
        value.get("input_hash", "legacy-input"),
        int(value.get("revision", 1)),
    )


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))

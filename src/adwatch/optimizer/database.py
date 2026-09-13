"""广告优化器专用 SQLite 数据库。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from adwatch.optimizer.models import AdMetric

LEGACY_COLLECTION_CLAIM_NOTE = "claim"
COLLECTION_CLAIM_SENTINEL = "__optimizer_collection_claim__"
OPTIMIZER_MIGRATION_TABLE = "optimizer_schema_migrations"


@dataclass(frozen=True)
class LegacyMigrationResult:
    """旧广告事实迁移的保守统计。"""

    migrated: int
    skipped: int


LegacyMetricMigrator = Callable[[sqlite3.Row], AdMetric | None]


class OptimizerDatabase:
    """与既有经营库隔离的、可迁移的优化器数据库。"""

    def __init__(
        self, path: Path, *, migrations: tuple[tuple[int, str], ...] | None = None
    ) -> None:
        self.path = path
        self.migrations = MIGRATIONS if migrations is None else migrations

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.create_function(
            "optimizer_recommendation_content_hash",
            1,
            _recommendation_payload_content_hash,
            deterministic=True,
        )
        connection.create_function(
            "optimizer_utc_epoch_us", 1, _utc_epoch_us, deterministic=True
        )
        connection.create_function("optimizer_utc_iso", 1, _utc_iso, deterministic=True)
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""CREATE TABLE IF NOT EXISTS {OPTIMIZER_MIGRATION_TABLE} (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"""
            )
            applied = {
                row[0]
                for row in connection.execute(
                    f"SELECT version FROM {OPTIMIZER_MIGRATION_TABLE}"
                )
            }
            for version, script in self.migrations:
                if version in applied:
                    continue
                for statement in _migration_statements(script):
                    connection.execute(statement)
                connection.execute(
                    f"INSERT INTO {OPTIMIZER_MIGRATION_TABLE} "
                    "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (version,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def schema_is_current(self) -> bool:
        """确认优化器迁移命名空间和全部核心表均完整。"""
        expected_versions = {version for version, _ in self.migrations}
        expected_tables = {
            "optimizer_ad_metrics",
            "optimizer_campaign_snapshots",
            "optimizer_campaign_profiles",
            "optimizer_campaign_profile_versions",
            "optimizer_recommendations",
            "optimizer_recommendation_feedback",
            "optimizer_recommendation_evaluations",
            "optimizer_run_logs",
            "optimizer_collection_days",
            "optimizer_collection_day_events",
            "optimizer_ad_metric_observations",
            OPTIMIZER_MIGRATION_TABLE,
        }
        with self.connect() as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return False
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not expected_tables <= tables:
                return False
            applied = {
                row[0]
                for row in connection.execute(
                    f"SELECT version FROM {OPTIMIZER_MIGRATION_TABLE}"
                )
            }
        return applied == expected_versions

    def backup(self, destination: Path) -> None:
        """使用 SQLite online backup API 创建一致性备份。"""
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("backup integrity check failed")
        finally:
            target.close()
            source.close()

    def integrity_check(self) -> str:
        with self.connect() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0])

    def migrate_legacy_metrics(
        self,
        migrator: LegacyMetricMigrator | None = None,
        *,
        legacy_database_path: Path | None = None,
        legacy_connection: sqlite3.Connection | None = None,
    ) -> LegacyMigrationResult:
        """只读迁移旧表中可明确证明身份的 Shopee 指标。

        旧 ``campaign_id`` 可能只是展示名称，所以默认迁移器只接收显式的
        ``stable_campaign_id`` 和 ``stable_item_id`` 列；不根据名称或 SKU 猜测。
        调用者可传入迁移器以适配已知的旧表标识列。
        """
        if (legacy_database_path is None) == (legacy_connection is None):
            raise ValueError(
                "provide exactly one legacy_database_path or legacy_connection"
            )
        rows = _read_legacy_rows(legacy_database_path, legacy_connection)
        self.migrate()
        with self.transaction() as connection:
            convert = migrator or _default_legacy_metric
            migrated = 0
            skipped = 0
            for row in rows:
                if str(row["platform"]).lower() != "shopee":
                    skipped += 1
                    continue
                metric = convert(row)
                if metric is None:
                    skipped += 1
                    continue
                cursor = connection.execute(
                    _METRIC_INSERT_IGNORE,
                    _metric_values(metric),
                )
                if cursor.rowcount:
                    connection.execute(
                        _METRIC_OBSERVATION_INSERT_IGNORE,
                        (*_metric_values(metric), 0),
                    )
                    migrated += 1
                else:
                    skipped += 1
            return LegacyMigrationResult(migrated, skipped)


def _read_legacy_rows(
    legacy_database_path: Path | None, legacy_connection: sqlite3.Connection | None
) -> list[sqlite3.Row]:
    if legacy_database_path is not None:
        source = sqlite3.connect(
            f"{legacy_database_path.resolve().as_uri()}?mode=ro", uri=True
        )
        source.row_factory = sqlite3.Row
        try:
            return _select_legacy_rows(source)
        finally:
            source.close()
    assert legacy_connection is not None
    original_factory = legacy_connection.row_factory
    legacy_connection.row_factory = sqlite3.Row
    try:
        return _select_legacy_rows(legacy_connection)
    finally:
        legacy_connection.row_factory = original_factory


def _select_legacy_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        ("daily_ad_metrics",),
    ).fetchone()
    if exists is None:
        return []
    return connection.execute("SELECT * FROM daily_ad_metrics").fetchall()


def _default_legacy_metric(row: sqlite3.Row) -> AdMetric | None:
    keys = set(row.keys())
    required = {
        "stable_campaign_id",
        "stable_item_id",
        "store",
        "data_date",
        "currency",
        "spend",
        "attributed_gmv",
        "orders",
        "source",
    }
    if not required <= keys:
        return None
    campaign_id = _required_text(row, "stable_campaign_id")
    item_id = _required_text(row, "stable_item_id")
    store_id = _required_text(row, "store")
    currency = _required_text(row, "currency")
    source = _required_text(row, "source")
    if None in (campaign_id, item_id, store_id, currency, source):
        return None
    try:
        data_date = date.fromisoformat(str(row["data_date"]))
        collected_at = _legacy_collected_at(row, data_date)
        return AdMetric(
            store_id=store_id,
            campaign_id=campaign_id,
            campaign_name=str(row["campaign_id"])
            if "campaign_id" in keys
            else campaign_id,
            item_id=item_id,
            data_date=data_date,
            currency=currency,
            spend=Decimal(str(row["spend"])),
            gmv=Decimal(str(row["attributed_gmv"])),
            orders=int(row["orders"]),
            collected_at=collected_at,
            source=source,
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None


def _legacy_collected_at(row: sqlite3.Row, fallback_date: date) -> datetime:
    collected_at = dict(row).get("collected_at")
    if collected_at:
        parsed = datetime.fromisoformat(str(collected_at))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed
    return datetime.combine(fallback_date, datetime.min.time(), UTC)


def _required_text(row: sqlite3.Row, field: str) -> str | None:
    value = row[field]
    return value if isinstance(value, str) and value.strip() else None


def _migration_statements(script: str) -> Iterator[str]:
    pending: list[str] = []
    for character in script:
        pending.append(character)
        candidate = "".join(pending).strip()
        if candidate and sqlite3.complete_statement(candidate):
            yield candidate
            pending.clear()
    if "".join(pending).strip():
        raise ValueError("migration contains an incomplete SQL statement")


def _metric_values(metric: AdMetric) -> tuple[str | int, ...]:
    collected_at = metric.collected_at.astimezone(UTC)
    return (
        metric.store_id,
        metric.campaign_id,
        metric.campaign_name,
        metric.item_id,
        metric.data_date.isoformat(),
        metric.currency,
        str(metric.spend),
        str(metric.gmv),
        metric.orders,
        collected_at.isoformat(timespec="microseconds"),
        _datetime_epoch_us(collected_at),
        metric.source,
        metric.grain.value,
    )


_METRIC_INSERT_IGNORE = """
    INSERT INTO optimizer_ad_metrics(
        store_id, campaign_id, campaign_name, item_id, data_date, currency,
        spend, gmv, orders, collected_at, collected_at_us, source, grain
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(store_id, campaign_id, item_id, data_date, grain) DO NOTHING
"""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS optimizer_ad_metrics (
    store_id TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT NOT NULL,
    item_id TEXT NOT NULL, data_date TEXT NOT NULL, currency TEXT NOT NULL,
    spend TEXT NOT NULL CHECK(CAST(spend AS REAL) >= 0),
    gmv TEXT NOT NULL CHECK(CAST(gmv AS REAL) >= 0),
    orders INTEGER NOT NULL CHECK(orders >= 0),
    collected_at TEXT NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY(store_id, campaign_id, item_id, data_date)
);
CREATE TABLE IF NOT EXISTS optimizer_campaign_snapshots (
    store_id TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT NOT NULL,
    campaign_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'paused', 'ended', 'unknown')),
    current_budget TEXT CHECK(current_budget IS NULL OR CAST(current_budget AS REAL) >= 0),
    target_roas TEXT CHECK(target_roas IS NULL OR CAST(target_roas AS REAL) > 0),
    currency TEXT NOT NULL, observed_at TEXT NOT NULL,
    PRIMARY KEY(store_id, campaign_id, observed_at)
);
CREATE TABLE IF NOT EXISTS optimizer_campaign_profiles (
    store_id TEXT NOT NULL, campaign_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0),
    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(store_id, campaign_id)
);
CREATE TABLE IF NOT EXISTS optimizer_campaign_profile_versions (
    store_id TEXT NOT NULL, campaign_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0),
    payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(store_id, campaign_id, version)
);
CREATE TABLE IF NOT EXISTS optimizer_recommendations (
    row_id INTEGER PRIMARY KEY, recommendation_id TEXT NOT NULL, store_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL, data_date TEXT NOT NULL, rule_version TEXT NOT NULL,
    action TEXT NOT NULL,
    input_hash TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0), content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL, generated_at TEXT NOT NULL,
    UNIQUE(store_id, recommendation_id, revision),
    UNIQUE(store_id, campaign_id, data_date, rule_version, action, revision)
);
CREATE TABLE IF NOT EXISTS optimizer_recommendation_feedback (
    feedback_id TEXT PRIMARY KEY,
    recommendation_row_id INTEGER NOT NULL REFERENCES optimizer_recommendations(row_id),
    status TEXT NOT NULL, actual_value TEXT, executed_at TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS optimizer_recommendation_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    recommendation_row_id INTEGER NOT NULL REFERENCES optimizer_recommendations(row_id),
    status TEXT NOT NULL, baseline_start TEXT NOT NULL, baseline_end TEXT NOT NULL,
    observation_start TEXT NOT NULL, observation_end TEXT NOT NULL,
    algorithm_version TEXT NOT NULL, evaluated_at TEXT NOT NULL, payload_json TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS optimizer_run_logs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed', 'no_data')),
    started_at TEXT NOT NULL,
    finished_at TEXT, details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS optimizer_collection_days (
    store_id TEXT NOT NULL, data_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed', 'no_data')),
    run_id TEXT REFERENCES optimizer_run_logs(run_id), note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(store_id, data_date)
);
"""

_METRIC_GRAIN_MIGRATION = """
ALTER TABLE optimizer_ad_metrics ADD COLUMN grain TEXT NOT NULL DEFAULT 'campaign'
CHECK(grain IN ('overview', 'campaign', 'item'));
"""

_COLLECTION_RETRY_MIGRATION = """
ALTER TABLE optimizer_collection_days ADD COLUMN attempted_at TEXT;
ALTER TABLE optimizer_collection_days ADD COLUMN next_retry_at TEXT;
"""

_COLLECTION_CLAIM_MIGRATION = """
ALTER TABLE optimizer_collection_days ADD COLUMN claim_token TEXT;
ALTER TABLE optimizer_collection_days ADD COLUMN claim_until TEXT;
"""

_SNAPSHOT_DATA_DATE_MIGRATION = """
ALTER TABLE optimizer_campaign_snapshots ADD COLUMN data_date TEXT;
UPDATE optimizer_campaign_snapshots SET data_date = '9999-12-31';
"""

_RECOMMENDATION_SLOT_MIGRATION = """
ALTER TABLE optimizer_recommendations ADD COLUMN slot TEXT NOT NULL DEFAULT 'primary';
UPDATE optimizer_recommendations
SET slot = CASE action
    WHEN 'create_group' THEN 'create_group'
    WHEN 'split_group' THEN 'split_group'
    ELSE 'primary'
END;
UPDATE optimizer_recommendations SET revision = revision + 1000000000;
WITH ranked AS (
    SELECT row_id,
           ROW_NUMBER() OVER (
               PARTITION BY store_id, campaign_id, data_date, rule_version, slot
               ORDER BY revision, generated_at, row_id
           ) AS next_revision
    FROM optimizer_recommendations
)
UPDATE optimizer_recommendations
SET revision = (SELECT next_revision FROM ranked WHERE ranked.row_id = optimizer_recommendations.row_id);
UPDATE optimizer_recommendations
SET payload_json = json_set(payload_json, '$.revision', revision, '$.slot', slot)
WHERE json_valid(payload_json);
UPDATE optimizer_recommendations
SET content_hash = optimizer_recommendation_content_hash(payload_json)
WHERE json_valid(payload_json);
UPDATE optimizer_recommendation_evaluations
SET payload_json = json_set(
    payload_json,
    '$.recommendation_revision',
    (SELECT revision FROM optimizer_recommendations
     WHERE optimizer_recommendations.row_id =
           optimizer_recommendation_evaluations.recommendation_row_id)
)
WHERE json_valid(payload_json);
CREATE UNIQUE INDEX IF NOT EXISTS optimizer_recommendation_slot_revision
ON optimizer_recommendations(store_id, campaign_id, data_date, rule_version, slot, revision);
"""

_METRIC_OBSERVATION_MIGRATION = """
CREATE TABLE optimizer_ad_metric_observations (
    row_id INTEGER PRIMARY KEY,
    store_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
    campaign_name TEXT NOT NULL, item_id TEXT NOT NULL,
    data_date TEXT NOT NULL, currency TEXT NOT NULL,
    spend TEXT NOT NULL CHECK(CAST(spend AS REAL) >= 0),
    gmv TEXT NOT NULL CHECK(CAST(gmv AS REAL) >= 0),
    orders INTEGER NOT NULL CHECK(orders >= 0),
    collected_at TEXT NOT NULL, source TEXT NOT NULL,
    grain TEXT NOT NULL CHECK(grain IN ('overview', 'campaign', 'item')),
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0, 1)),
    UNIQUE(
        store_id, campaign_id, item_id, data_date, collected_at,
        spend, gmv, orders, source, grain, is_deleted
    )
);
INSERT OR IGNORE INTO optimizer_ad_metric_observations(
    store_id,campaign_id,campaign_name,item_id,data_date,currency,
    spend,gmv,orders,collected_at,source,grain,is_deleted
)
SELECT store_id,campaign_id,campaign_name,item_id,data_date,currency,
       spend,gmv,orders,collected_at,source,grain,0
FROM optimizer_ad_metrics;
"""

_EVALUATION_REVISION_MIGRATION = """
ALTER TABLE optimizer_recommendation_evaluations
ADD COLUMN feedback_id TEXT NOT NULL DEFAULT 'legacy-feedback';
ALTER TABLE optimizer_recommendation_evaluations
ADD COLUMN input_hash TEXT NOT NULL DEFAULT 'legacy-input';
ALTER TABLE optimizer_recommendation_evaluations
ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0);
UPDATE optimizer_recommendation_evaluations
SET feedback_id = COALESCE(
        json_extract(payload_json, '$.feedback_id'),
        (SELECT feedback.feedback_id
         FROM optimizer_recommendation_feedback AS feedback
         WHERE feedback.recommendation_row_id =
               optimizer_recommendation_evaluations.recommendation_row_id
         ORDER BY feedback.executed_at, feedback.feedback_id
         LIMIT 1),
        'legacy-feedback:' || recommendation_row_id
    ),
    input_hash = COALESCE(
        json_extract(payload_json, '$.input_hash'),
        'legacy-input:' || evaluation_id
    );
WITH duplicates AS (
    SELECT evaluation_id, input_hash,
           COUNT(*) OVER (
               PARTITION BY recommendation_row_id, feedback_id, input_hash
           ) AS duplicate_count
    FROM optimizer_recommendation_evaluations
)
UPDATE optimizer_recommendation_evaluations
SET input_hash = 'legacy-migrated:' || evaluation_id || ':' || input_hash
WHERE evaluation_id IN (
    SELECT evaluation_id FROM duplicates
    WHERE input_hash = 'legacy-input' OR duplicate_count > 1
);
WITH ranked AS (
    SELECT evaluation_id,
           ROW_NUMBER() OVER (
               PARTITION BY recommendation_row_id, feedback_id
               ORDER BY evaluated_at, evaluation_id
           ) AS next_revision
    FROM optimizer_recommendation_evaluations
)
UPDATE optimizer_recommendation_evaluations
SET revision = (
    SELECT next_revision FROM ranked
    WHERE ranked.evaluation_id = optimizer_recommendation_evaluations.evaluation_id
);
UPDATE optimizer_recommendation_evaluations
SET payload_json = json_set(
    payload_json,
    '$.feedback_id', feedback_id,
    '$.input_hash', input_hash,
    '$.revision', revision
)
WHERE json_valid(payload_json);
CREATE UNIQUE INDEX optimizer_evaluation_feedback_revision
ON optimizer_recommendation_evaluations(
    recommendation_row_id, feedback_id, revision
);
CREATE UNIQUE INDEX optimizer_evaluation_feedback_input
ON optimizer_recommendation_evaluations(
    recommendation_row_id, feedback_id, input_hash
);
"""

_METRIC_CURRENT_GRAIN_KEY_MIGRATION = """
ALTER TABLE optimizer_ad_metrics RENAME TO optimizer_ad_metrics_legacy_v9;
CREATE TABLE optimizer_ad_metrics (
    store_id TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT NOT NULL,
    item_id TEXT NOT NULL, data_date TEXT NOT NULL, currency TEXT NOT NULL,
    spend TEXT NOT NULL CHECK(CAST(spend AS REAL) >= 0),
    gmv TEXT NOT NULL CHECK(CAST(gmv AS REAL) >= 0),
    orders INTEGER NOT NULL CHECK(orders >= 0),
    collected_at TEXT NOT NULL, source TEXT NOT NULL,
    grain TEXT NOT NULL CHECK(grain IN ('overview', 'campaign', 'item')),
    PRIMARY KEY(store_id, campaign_id, item_id, data_date, grain)
);
INSERT INTO optimizer_ad_metrics(
    store_id,campaign_id,campaign_name,item_id,data_date,currency,
    spend,gmv,orders,collected_at,source,grain
)
WITH latest AS (
    SELECT observation.*,
           ROW_NUMBER() OVER (
               PARTITION BY store_id,campaign_id,item_id,data_date,grain
               ORDER BY datetime(collected_at) DESC, row_id DESC
           ) AS current_rank
    FROM optimizer_ad_metric_observations AS observation
)
SELECT store_id,campaign_id,campaign_name,item_id,data_date,currency,
       spend,gmv,orders,collected_at,source,grain
FROM latest
WHERE current_rank=1 AND is_deleted=0;
INSERT OR IGNORE INTO optimizer_ad_metrics(
    store_id,campaign_id,campaign_name,item_id,data_date,currency,
    spend,gmv,orders,collected_at,source,grain
)
SELECT legacy.store_id,legacy.campaign_id,legacy.campaign_name,legacy.item_id,
       legacy.data_date,legacy.currency,legacy.spend,legacy.gmv,legacy.orders,
       legacy.collected_at,legacy.source,legacy.grain
FROM optimizer_ad_metrics_legacy_v9 AS legacy
WHERE NOT EXISTS (
    SELECT 1 FROM optimizer_ad_metric_observations AS observation
    WHERE observation.store_id=legacy.store_id
      AND observation.campaign_id=legacy.campaign_id
      AND observation.item_id=legacy.item_id
      AND observation.data_date=legacy.data_date
      AND observation.grain=legacy.grain
);
DROP TABLE optimizer_ad_metrics_legacy_v9;
"""

_UTC_MICROSECOND_AND_QUERY_INDEX_MIGRATION = """
ALTER TABLE optimizer_ad_metrics ADD COLUMN collected_at_us INTEGER;
ALTER TABLE optimizer_ad_metric_observations ADD COLUMN collected_at_us INTEGER;
ALTER TABLE optimizer_campaign_snapshots ADD COLUMN observed_at_us INTEGER;
ALTER TABLE optimizer_recommendation_feedback ADD COLUMN executed_at_us INTEGER;
ALTER TABLE optimizer_recommendation_evaluations ADD COLUMN evaluated_at_us INTEGER;
UPDATE optimizer_ad_metrics
SET collected_at_us=optimizer_utc_epoch_us(collected_at);
UPDATE optimizer_ad_metric_observations
SET collected_at_us=optimizer_utc_epoch_us(collected_at);
UPDATE optimizer_campaign_snapshots
SET observed_at_us=optimizer_utc_epoch_us(observed_at);
UPDATE optimizer_recommendation_feedback
SET executed_at_us=optimizer_utc_epoch_us(executed_at);
UPDATE optimizer_recommendation_evaluations
SET evaluated_at_us=optimizer_utc_epoch_us(evaluated_at);
DELETE FROM optimizer_ad_metrics AS current
WHERE EXISTS (
    SELECT 1 FROM optimizer_ad_metric_observations AS observation
    WHERE observation.store_id=current.store_id
      AND observation.campaign_id=current.campaign_id
      AND observation.item_id=current.item_id
      AND observation.data_date=current.data_date
      AND observation.grain=current.grain
);
INSERT INTO optimizer_ad_metrics(
    store_id,campaign_id,campaign_name,item_id,data_date,currency,
    spend,gmv,orders,collected_at,source,grain,collected_at_us
)
WITH latest AS (
    SELECT observation.*,
           ROW_NUMBER() OVER (
               PARTITION BY store_id,campaign_id,item_id,data_date,grain
               ORDER BY collected_at_us DESC, row_id DESC
           ) AS current_rank
    FROM optimizer_ad_metric_observations AS observation
)
SELECT store_id,campaign_id,campaign_name,item_id,data_date,currency,
       spend,gmv,orders,collected_at,source,grain,collected_at_us
FROM latest
WHERE current_rank=1 AND is_deleted=0;
CREATE INDEX optimizer_observation_store_day_time
ON optimizer_ad_metric_observations(store_id,data_date,collected_at_us);
CREATE INDEX optimizer_observation_campaign_window
ON optimizer_ad_metric_observations(
    store_id,campaign_id,data_date,collected_at_us,grain,item_id,is_deleted
);
CREATE INDEX optimizer_feedback_store_lookup
ON optimizer_recommendation_feedback(recommendation_row_id,feedback_id);
CREATE INDEX optimizer_snapshot_decision_lookup
ON optimizer_campaign_snapshots(store_id,campaign_id,data_date,observed_at_us);
CREATE INDEX optimizer_evaluation_latest_feedback
ON optimizer_recommendation_evaluations(
    recommendation_row_id,feedback_id,revision DESC,evaluated_at_us
);
CREATE INDEX optimizer_recommendation_effectiveness
ON optimizer_recommendations(store_id,action,campaign_id,row_id);
"""

_COLLECTION_DAY_EVENT_MIGRATION = f"""
CREATE TABLE optimizer_collection_day_events (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    data_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed', 'no_data')),
    run_id TEXT REFERENCES optimizer_run_logs(run_id),
    note TEXT NOT NULL DEFAULT '',
    attempted_at TEXT NOT NULL,
    attempted_at_us INTEGER NOT NULL
);
INSERT INTO optimizer_collection_day_events(
    store_id,data_date,status,run_id,note,attempted_at,attempted_at_us
)
SELECT store_id,data_date,status,run_id,note,
       optimizer_utc_iso(COALESCE(attempted_at, data_date || 'T00:00:00+00:00')),
       optimizer_utc_epoch_us(COALESCE(attempted_at, data_date || 'T00:00:00+00:00'))
FROM optimizer_collection_days
WHERE NOT (
    run_id IS NULL AND note IN (
        '{LEGACY_COLLECTION_CLAIM_NOTE}', '{COLLECTION_CLAIM_SENTINEL}'
    )
);
CREATE INDEX optimizer_collection_event_store_day_time
ON optimizer_collection_day_events(
    store_id,data_date,attempted_at_us DESC,row_id DESC
);
CREATE UNIQUE INDEX optimizer_collection_event_identity
ON optimizer_collection_day_events(
    store_id,data_date,status,IFNULL(run_id,''),note,attempted_at_us
);
"""

_REPORT_OBSERVATION_INDEX_MIGRATION = """
CREATE INDEX optimizer_observation_report_date
ON optimizer_ad_metric_observations(
    store_id,data_date DESC,grain,collected_at_us DESC,is_deleted,
    campaign_id,item_id,row_id
);
CREATE INDEX optimizer_observation_logical_latest
ON optimizer_ad_metric_observations(
    store_id,campaign_id,item_id,data_date,grain,
    collected_at_us DESC,row_id DESC,is_deleted
);
"""

_CAMPAIGN_RECENT_OBSERVATION_INDEX_MIGRATION = """
CREATE INDEX optimizer_observation_campaign_recent
ON optimizer_ad_metric_observations(
    store_id,grain,data_date DESC,campaign_id,collected_at_us DESC,row_id DESC,
    item_id,is_deleted
);
"""

MIGRATIONS = (
    (1, _SCHEMA),
    (2, _METRIC_GRAIN_MIGRATION),
    (3, _COLLECTION_RETRY_MIGRATION),
    (4, _COLLECTION_CLAIM_MIGRATION),
    (5, _SNAPSHOT_DATA_DATE_MIGRATION),
    (6, _RECOMMENDATION_SLOT_MIGRATION),
    (7, _METRIC_OBSERVATION_MIGRATION),
    (8, _EVALUATION_REVISION_MIGRATION),
    (9, _METRIC_CURRENT_GRAIN_KEY_MIGRATION),
    (10, _UTC_MICROSECOND_AND_QUERY_INDEX_MIGRATION),
    (11, _COLLECTION_DAY_EVENT_MIGRATION),
    (12, _REPORT_OBSERVATION_INDEX_MIGRATION),
    (13, _CAMPAIGN_RECENT_OBSERVATION_INDEX_MIGRATION),
)


_METRIC_OBSERVATION_INSERT_IGNORE = """
INSERT OR IGNORE INTO optimizer_ad_metric_observations(
    store_id,campaign_id,campaign_name,item_id,data_date,currency,
    spend,gmv,orders,collected_at,collected_at_us,source,grain,is_deleted
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _recommendation_payload_content_hash(payload: str) -> str:
    """按仓储当前内容哈希口径修复迁移中的建议 payload。"""
    value = json.loads(payload)
    for key in ("recommendation_id", "revision", "generated_at"):
        value.pop(key, None)
    for evidence in value.get("evidence", []):
        quality = evidence.get("quality")
        if isinstance(quality, dict):
            quality.pop("assessed_at", None)
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _utc_epoch_us(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return _datetime_epoch_us(parsed.astimezone(UTC))


def _utc_iso(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _datetime_epoch_us(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds

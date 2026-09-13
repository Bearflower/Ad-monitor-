from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from adwatch.optimizer.database import MIGRATIONS, OptimizerDatabase
from adwatch.optimizer.models import (
    Action,
    Confidence,
    DataQuality,
    DataQualityStatus,
    Evaluation,
    EvaluationStatus,
    Evidence,
    Recommendation,
    RollbackCondition,
    SuccessCondition,
    WindowStats,
)
from adwatch.optimizer.repository import (
    _PENDING_FEEDBACK_SQL,
    OptimizerRepository,
    _json,
)


def _legacy_recommendation_payload(
    recommendation_id: str, action: Action, revision: int = 1
) -> str:
    now = datetime(2026, 9, 11, tzinfo=UTC)
    window = WindowStats(
        date(2026, 9, 8), date(2026, 9, 10), Decimal(100), Decimal(250), 5
    )
    numeric = action in {Action.INCREASE_BUDGET, Action.DECREASE_BUDGET}
    increase = action is Action.INCREASE_BUDGET
    payload = json.loads(
        _json(
            Recommendation(
                recommendation_id,
                "store",
                "campaign",
                date(2026, 9, 10),
                action,
                Confidence.HIGH,
                2,
                Decimal(100) if numeric else None,
                Decimal(110 if increase else 90) if numeric else None,
                Decimal("0.1" if increase else "-0.1") if numeric else None,
                (
                    Evidence(
                        action.value,
                        "历史建议",
                        window,
                        window,
                        DataQuality(DataQualityStatus.HEALTHY, now),
                        (("legacy", "true"),),
                    ),
                ),
                SuccessCondition(Decimal(2), Decimal("0.05"), 3),
                RollbackCondition(Decimal("1.8"), 2),
                Decimal("0.1"),
                Decimal("0.1"),
                3,
                "v1",
                recommendation_id,
                revision,
                now,
            )
        )
    )
    payload.pop("slot")
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_migrate_is_idempotent_and_configures_sqlite(tmp_path: Path):
    database = OptimizerDatabase(tmp_path / "nested" / "optimizer.sqlite3")

    database.migrate()
    database.migrate()

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert {name for name in tables if name.startswith("optimizer_")} == {
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
            "optimizer_schema_migrations",
        }
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] > 0
        assert {
            row["version"]
            for row in connection.execute(
                "SELECT version FROM optimizer_schema_migrations"
            )
        } == set(range(1, 14))
        evaluation_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(optimizer_recommendation_evaluations)"
            )
        }
        assert {"feedback_id", "input_hash", "revision"} <= evaluation_columns


def test_optimizer_migrations_do_not_reuse_legacy_global_versions(tmp_path: Path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations VALUES (?, 'legacy')",
            ((version,) for version in range(1, 43)),
        )
        connection.execute("CREATE TABLE legacy_orders(id INTEGER PRIMARY KEY)")

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM optimizer_schema_migrations"
        ).fetchone()[0] == len(MIGRATIONS)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 42
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='optimizer_run_logs'"
        ).fetchone() is not None


def test_v10_observation_indexes_match_core_query_plans(tmp_path: Path):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()

    with database.connect() as connection:
        campaign_plan = connection.execute(
            """EXPLAIN QUERY PLAN SELECT * FROM optimizer_ad_metric_observations
            WHERE store_id=? AND campaign_id=? AND data_date BETWEEN ? AND ?
              AND collected_at_us<=? AND grain='campaign'""",
            ("store", "campaign", "2026-09-01", "2026-09-30", 1),
        ).fetchall()
        day_plan = connection.execute(
            """EXPLAIN QUERY PLAN SELECT * FROM optimizer_ad_metric_observations
            WHERE store_id=? AND data_date=? AND collected_at_us<=?""",
            ("store", "2026-09-01", 1),
        ).fetchall()

    assert any(
        index_name in row["detail"]
        for row in campaign_plan
        for index_name in (
            "optimizer_observation_campaign_window",
            "optimizer_observation_campaign_recent",
        )
    )
    assert any(
        "optimizer_observation_store_day_time" in row["detail"] for row in day_plan
    )


def test_pending_feedback_plan_is_store_scoped_and_uses_latest_feedback_index(
    tmp_path: Path,
):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()

    with database.connect() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN " + _PENDING_FEEDBACK_SQL, ("store",)
        ).fetchall()

    details = tuple(row["detail"] for row in plan)
    assert not any("MATERIALIZE" in detail.upper() for detail in details)
    assert any(
        index_name in detail
        for detail in details
        for index_name in (
            "optimizer_evaluation_latest_feedback",
            "optimizer_evaluation_feedback_revision",
        )
    )


def test_failed_versioned_migration_rolls_back_all_statements(tmp_path: Path):
    database = OptimizerDatabase(
        tmp_path / "optimizer.sqlite3",
        migrations=((1, "CREATE TABLE rollback_probe(id INTEGER); INVALID SQL;"),),
    )

    with pytest.raises(sqlite3.OperationalError):
        database.migrate()

    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rollback_probe'"
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='optimizer_schema_migrations'"
            ).fetchone()
            is None
        )


def test_v4_database_upgrades_to_v6_without_losing_legacy_rows(tmp_path: Path):
    path = tmp_path / "optimizer.sqlite3"
    OptimizerDatabase(path, migrations=MIGRATIONS[:4]).migrate()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO optimizer_campaign_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "store",
                "campaign",
                "名称",
                "auto",
                "active",
                "100",
                "2",
                "CNY",
                "2026-09-10T16:30:00+00:00",
            ),
        )
        connection.execute(
            """INSERT INTO optimizer_recommendations(
                recommendation_id, store_id, campaign_id, data_date, rule_version,
                action, input_hash, revision, content_hash, payload_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-decrease",
                "store",
                "campaign",
                "2026-09-10",
                "v1",
                "decrease_budget",
                "hash-2",
                1,
                "content-2",
                "{}",
                "2026-09-11T00:00:00+00:00",
            ),
        )
        connection.execute(
            """INSERT INTO optimizer_recommendations(
                recommendation_id, store_id, campaign_id, data_date, rule_version,
                action, input_hash, revision, content_hash, payload_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy",
                "store",
                "campaign",
                "2026-09-10",
                "v1",
                "hold",
                "hash",
                1,
                "content",
                "{}",
                "2026-09-10T00:00:00+00:00",
            ),
        )
        connection.commit()

    upgraded = OptimizerDatabase(path)
    upgraded.migrate()
    upgraded.migrate()
    repository = OptimizerRepository(upgraded)

    with upgraded.connect() as connection:
        snapshot = connection.execute(
            "SELECT data_date FROM optimizer_campaign_snapshots"
        ).fetchone()
        recommendations = connection.execute(
            "SELECT action, slot, revision FROM optimizer_recommendations ORDER BY revision"
        ).fetchall()
        versions = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM optimizer_schema_migrations"
            )
        }
    assert snapshot[0] == "9999-12-31"
    assert [(row[0], row[1], row[2]) for row in recommendations] == [
        ("hold", "primary", 1),
        ("decrease_budget", "primary", 2),
    ]
    assert (
        repository.latest_snapshot_for("store", "campaign", date(2026, 9, 10)) is None
    )
    assert (
        repository.latest_snapshot_for("store", "campaign", date(2026, 9, 11)) is None
    )
    with upgraded.transaction() as connection:
        connection.execute(
            """INSERT INTO optimizer_campaign_snapshots(
                store_id, campaign_id, campaign_name, campaign_type, status,
                current_budget, target_roas, currency, observed_at, data_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "store",
                "campaign",
                "新快照",
                "auto",
                "active",
                "100",
                "2",
                "CNY",
                "2026-09-11T01:00:00+00:00",
                "2026-09-11",
            ),
        )
    assert (
        repository.latest_snapshot_for("store", "campaign", date(2026, 9, 11))
        is not None
    )
    assert {1, 2, 3, 4, 5, 6} <= versions


def test_v6_rewrites_payload_revision_and_preserves_physical_feedback_binding(
    tmp_path: Path,
):
    path = tmp_path / "optimizer.sqlite3"
    OptimizerDatabase(path, migrations=MIGRATIONS[:5]).migrate()
    decrease_payload = _legacy_recommendation_payload(
        "legacy-decrease", Action.DECREASE_BUDGET
    )
    hold_payload = _legacy_recommendation_payload("legacy-hold", Action.HOLD)
    create_payload = _legacy_recommendation_payload(
        "legacy-create", Action.CREATE_GROUP
    )
    with sqlite3.connect(path) as connection:
        rows = (
            (
                "legacy-hold",
                "hold",
                "legacy-hold",
                hold_payload,
                "2026-09-10T00:00:00+00:00",
            ),
            (
                "legacy-decrease",
                "decrease_budget",
                "legacy-decrease",
                decrease_payload,
                "2026-09-11T00:00:00+00:00",
            ),
            (
                "legacy-create",
                "create_group",
                "legacy-create",
                create_payload,
                "2026-09-11T01:00:00+00:00",
            ),
        )
        row_ids: dict[str, int] = {}
        for recommendation_id, action, input_hash, payload, generated_at in rows:
            cursor = connection.execute(
                """INSERT INTO optimizer_recommendations(
                    recommendation_id,store_id,campaign_id,data_date,rule_version,
                    action,input_hash,revision,content_hash,payload_json,generated_at
                ) VALUES (?, 'store', 'campaign', '2026-09-10', 'v1', ?, ?, 1,
                          'legacy-content', ?, ?)""",
                (recommendation_id, action, input_hash, payload, generated_at),
            )
            row_ids[recommendation_id] = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO optimizer_recommendation_feedback
            VALUES ('feedback-legacy', ?, 'executed', NULL,
                    '2026-09-12T00:00:00+00:00', '')""",
            (row_ids["legacy-decrease"],),
        )
        window = WindowStats(
            date(2026, 9, 1), date(2026, 9, 3), Decimal(100), Decimal(250), 5
        )
        after = WindowStats(
            date(2026, 9, 5), date(2026, 9, 7), Decimal(90), Decimal(250), 5
        )
        evaluation = Evaluation(
            "evaluation-legacy",
            "legacy-decrease",
            EvaluationStatus.EFFECTIVE,
            window.start_date,
            window.end_date,
            after.start_date,
            after.end_date,
            "v1",
            datetime(2026, 9, 12, tzinfo=UTC),
            window,
            after,
            recommendation_revision=1,
            feedback_id="feedback-legacy",
            applied_value=Decimal(90),
            input_hash="evaluation-input",
        )
        connection.execute(
            """INSERT INTO optimizer_recommendation_evaluations(
                evaluation_id,recommendation_row_id,status,baseline_start,baseline_end,
                observation_start,observation_end,algorithm_version,evaluated_at,
                payload_json,note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')""",
            (
                evaluation.evaluation_id,
                row_ids["legacy-decrease"],
                evaluation.status.value,
                evaluation.baseline_start.isoformat(),
                evaluation.baseline_end.isoformat(),
                evaluation.observation_start.isoformat(),
                evaluation.observation_end.isoformat(),
                evaluation.algorithm_version,
                evaluation.evaluated_at.isoformat(),
                _json(evaluation),
            ),
        )
        connection.commit()

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()
    repository = OptimizerRepository(database)

    decrease = repository.get_recommendation("store", "legacy-decrease", 2)
    assert decrease is not None
    assert decrease.revision == 2
    assert decrease.slot.value == "primary"
    assert repository.get_recommendation("store", "legacy-create", 1).slot.value == (
        "create_group"
    )
    migrated_create = repository.get_recommendation("store", "legacy-create", 1)
    assert repository.save_recommendation(migrated_create).revision == 1
    assert (
        repository.list_feedback("store", "legacy-decrease")[0].recommendation_revision
        == 2
    )
    assert (
        repository.list_evaluations("store", "legacy-decrease")[
            0
        ].recommendation_revision
        == 2
    )


def test_v8_migrates_duplicate_legacy_evaluation_hashes_to_unique_stable_inputs(
    tmp_path: Path,
):
    path = tmp_path / "optimizer.sqlite3"
    OptimizerDatabase(path, migrations=MIGRATIONS[:7]).migrate()
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """INSERT INTO optimizer_recommendations(
                recommendation_id,store_id,campaign_id,data_date,rule_version,
                action,slot,input_hash,revision,content_hash,payload_json,generated_at
            ) VALUES ('legacy-rec','store','campaign','2026-09-10','v1',
                      'hold','primary','input',1,'content','{}',
                      '2026-09-11T00:00:00+00:00')"""
        )
        row_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO optimizer_recommendation_feedback
            VALUES ('legacy-feedback', ?, 'executed', NULL,
                    '2026-09-12T00:00:00+00:00', '')""",
            (row_id,),
        )
        for index in (1, 2):
            payload = json.dumps(
                {
                    "feedback_id": "legacy-feedback",
                    "input_hash": "legacy-input",
                    "revision": 1,
                }
            )
            connection.execute(
                """INSERT INTO optimizer_recommendation_evaluations(
                    evaluation_id,recommendation_row_id,status,baseline_start,baseline_end,
                    observation_start,observation_end,algorithm_version,evaluated_at,
                    payload_json,note
                ) VALUES (?, ?, 'pending', '2026-09-01', '2026-09-03',
                          '2026-09-05', '2026-09-07', 'v1', ?, ?, '')""",
                (
                    f"legacy-evaluation-{index}",
                    row_id,
                    f"2026-09-{12 + index:02d}T00:00:00+00:00",
                    payload,
                ),
            )
        connection.commit()

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        rows = connection.execute(
            """SELECT evaluation_id,input_hash,revision,payload_json
            FROM optimizer_recommendation_evaluations ORDER BY revision"""
        ).fetchall()
    assert [row["revision"] for row in rows] == [1, 2]
    assert len({row["input_hash"] for row in rows}) == 2
    for row in rows:
        payload = json.loads(row["payload_json"])
        assert payload["input_hash"] == row["input_hash"]
        assert payload["revision"] == row["revision"]


def test_v9_rebuilds_current_from_history_tombstones_and_legacy_fallback(
    tmp_path: Path,
):
    path = tmp_path / "optimizer.sqlite3"
    OptimizerDatabase(path, migrations=MIGRATIONS[:8]).migrate()
    with sqlite3.connect(path) as connection:
        observations = (
            ("shared", "campaign", "10", 0, "2026-09-08T09:00:00+00:00"),
            ("shared", "item", "99", 0, "2026-09-08T09:00:00+00:00"),
            ("gone", "campaign", "20", 0, "2026-09-08T09:00:00+00:00"),
            ("gone", "campaign", "20", 1, "2026-09-09T09:00:00+00:00"),
        )
        for item_id, grain, spend, is_deleted, collected_at in observations:
            connection.execute(
                """INSERT INTO optimizer_ad_metric_observations(
                    store_id,campaign_id,campaign_name,item_id,data_date,currency,
                    spend,gmv,orders,collected_at,source,grain,is_deleted
                ) VALUES ('store','campaign','名称',?,'2026-09-07','CNY',
                          ?,'100',1,?,'legacy',?,?)""",
                (item_id, spend, collected_at, grain, is_deleted),
            )
        current_rows = (
            ("shared", "99", "item"),
            ("gone", "20", "campaign"),
            ("fallback", "30", "campaign"),
        )
        for item_id, spend, grain in current_rows:
            connection.execute(
                """INSERT INTO optimizer_ad_metrics(
                    store_id,campaign_id,campaign_name,item_id,data_date,currency,
                    spend,gmv,orders,collected_at,source,grain
                ) VALUES ('store','campaign','名称',?,'2026-09-07','CNY',
                          ?,'100',1,'2026-09-10T09:00:00+00:00','legacy',?)""",
                (item_id, spend, grain),
            )
        connection.commit()

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        rows = connection.execute(
            """SELECT item_id,grain,spend FROM optimizer_ad_metrics
            WHERE store_id='store' AND campaign_id='campaign'
            ORDER BY item_id,grain"""
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("fallback", "campaign", "30"),
        ("shared", "campaign", "10"),
        ("shared", "item", "99"),
    ]


def test_transaction_rolls_back_and_foreign_keys_are_enforced(tmp_path: Path):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()

    with (
        pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"),
        database.transaction() as connection,
    ):
        connection.execute(
            "INSERT INTO optimizer_run_logs(run_id, status, started_at) "
            "VALUES ('run-1', 'success', '2026-09-09T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO optimizer_collection_days(store_id, data_date, status, run_id, note) "
            "VALUES ('store-1', '2026-09-09', 'success', 'missing-run', '')"
        )
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM optimizer_run_logs").fetchone()[0]
            == 0
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO optimizer_recommendation_feedback("
                "feedback_id, recommendation_row_id, status, executed_at, note"
                ") VALUES ('feedback-1', 999, 'executed', '2026-09-09T00:00:00+00:00', '')"
            )


def test_backup_and_integrity_check(tmp_path: Path):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO optimizer_run_logs(run_id, status, started_at) "
            "VALUES ('run-1', 'success', '2026-09-09T00:00:00+00:00')"
        )
    backup = tmp_path / "backups" / "optimizer.sqlite3"

    database.backup(backup)

    assert backup.exists()
    assert database.integrity_check() == "ok"
    with sqlite3.connect(backup) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM optimizer_run_logs").fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(FileExistsError):
        database.backup(backup)


def test_v11_seeds_collection_events_with_exact_utc_microseconds(tmp_path: Path):
    path = tmp_path / "optimizer.sqlite3"
    OptimizerDatabase(path, migrations=MIGRATIONS[:10]).migrate()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO optimizer_collection_days("
            "store_id,data_date,status,note,attempted_at) VALUES (?, ?, ?, ?, ?)",
            (
                "store-1",
                "2026-09-09",
                "no_data",
                "legacy",
                "2026-09-10T09:00:00.900000+08:00",
            ),
        )

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        row = connection.execute(
            "SELECT status,note,attempted_at,attempted_at_us "
            "FROM optimizer_collection_day_events"
        ).fetchone()
        indexes = {
            item[1]
            for item in connection.execute(
                "PRAGMA index_list('optimizer_collection_day_events')"
            )
        }
    expected = datetime(2026, 9, 10, 1, 0, 0, 900_000, tzinfo=UTC)
    assert tuple(row) == (
        "no_data",
        "legacy",
        expected.isoformat(timespec="microseconds"),
        int(expected.timestamp() * 1_000_000),
    )
    assert "optimizer_collection_event_store_day_time" in indexes


@pytest.mark.parametrize("claim_state", ["active", "released", "expired"])
def test_v11_does_not_seed_claim_placeholder_as_collection_result(
    tmp_path: Path, claim_state: str
):
    path = tmp_path / "optimizer.sqlite3"
    database_v10 = OptimizerDatabase(path, migrations=MIGRATIONS[:10])
    database_v10.migrate()
    repository = OptimizerRepository(database_v10)
    claim_until = datetime(
        2000 if claim_state == "expired" else 2099, 9, 10, 1, 5, tzinfo=UTC
    )
    with database_v10.transaction() as connection:
        connection.execute(
            "INSERT INTO optimizer_collection_days("
            "store_id,data_date,status,note,attempted_at,claim_token,claim_until) "
            "VALUES (?, ?, 'failed', 'claim', ?, 'owner', ?)",
            (
                "store-1",
                "2026-09-09",
                "2026-09-10T01:00:00+00:00",
                claim_until.isoformat(),
            ),
        )
    if claim_state == "released":
        repository.release_collection_claim("store-1", date(2026, 9, 9), "owner")

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM optimizer_collection_day_events"
            ).fetchone()[0]
            == 0
        )


def test_v12_adds_report_observation_indexes_idempotently(tmp_path: Path):
    path = tmp_path / "optimizer.sqlite3"
    OptimizerDatabase(path, migrations=MIGRATIONS[:11]).migrate()

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert {
        "optimizer_observation_report_date",
        "optimizer_observation_logical_latest",
    } <= indexes


def test_v13_adds_campaign_recent_observation_index_idempotently(tmp_path: Path):
    path = tmp_path / "optimizer.sqlite3"
    OptimizerDatabase(path, migrations=MIGRATIONS[:12]).migrate()

    database = OptimizerDatabase(path)
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert "optimizer_observation_campaign_recent" in indexes


def test_legacy_migration_only_accepts_shopee_with_explicit_stable_ids(tmp_path: Path):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    legacy_path = tmp_path / "legacy.sqlite3"
    database.migrate()
    with sqlite3.connect(legacy_path) as connection:
        connection.execute(
            """
            CREATE TABLE daily_ad_metrics (
                platform TEXT, store TEXT, campaign_id TEXT, sku_id TEXT,
                data_date TEXT, currency TEXT, spend TEXT, attributed_gmv TEXT,
                orders INTEGER, source TEXT, stable_campaign_id TEXT,
                stable_item_id TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO daily_ad_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "shopee",
                    "store-1",
                    "展示名",
                    "SKU-1",
                    "2026-09-07",
                    "CNY",
                    "1.20",
                    "3.40",
                    2,
                    "legacy",
                    "campaign-1",
                    "item-1",
                ),
                (
                    "tiktok",
                    "store-1",
                    "展示名",
                    "SKU-2",
                    "2026-09-07",
                    "CNY",
                    "9",
                    "10",
                    1,
                    "legacy",
                    "campaign-2",
                    "item-2",
                ),
                (
                    "shopee",
                    "store-1",
                    "展示名",
                    "SKU-3",
                    "2026-09-07",
                    "CNY",
                    "9",
                    "10",
                    1,
                    "legacy",
                    None,
                    None,
                ),
                (
                    "shopee",
                    None,
                    "展示名",
                    "SKU-4",
                    "2026-09-07",
                    "CNY",
                    "9",
                    "10",
                    1,
                    "",
                    "campaign-4",
                    "item-4",
                ),
            ],
        )

    result = database.migrate_legacy_metrics(legacy_database_path=legacy_path)
    repeat = database.migrate_legacy_metrics(legacy_database_path=legacy_path)

    assert (result.migrated, result.skipped) == (1, 3)
    assert (repeat.migrated, repeat.skipped) == (0, 4)
    with sqlite3.connect(legacy_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM daily_ad_metrics").fetchone()[0]
            == 4
        )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT campaign_id, item_id, spend FROM optimizer_ad_metrics"
        ).fetchone()
    assert tuple(row) == ("campaign-1", "item-1", "1.20")


@pytest.mark.parametrize("status", ["success", "failed", "no_data"])
def test_status_constraints_allow_only_collection_outcomes(tmp_path: Path, status: str):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()

    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO optimizer_run_logs(run_id, status, started_at) VALUES (?, ?, ?)",
            (f"run-{status}", status, "2026-09-09T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO optimizer_collection_days(store_id, data_date, status, note) VALUES (?, ?, ?, '')",
            ("store-1", f"2026-09-0{len(status)}", status),
        )


def test_status_constraints_reject_unknown_value(tmp_path: Path):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()

    with database.transaction() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO optimizer_run_logs(run_id, status, started_at) VALUES ('bad', 'running', '2026-09-09T00:00:00+00:00')"
        )


def test_recommendation_logical_revision_is_unique_in_database(tmp_path: Path):
    database = OptimizerDatabase(tmp_path / "optimizer.sqlite3")
    database.migrate()
    values = (
        "rec-1",
        "store-1",
        "campaign-1",
        "2026-09-09",
        "v1",
        "hold",
        "input",
        1,
        "hash",
        "{}",
        "2026-09-09T00:00:00+00:00",
    )

    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO optimizer_recommendations(
                recommendation_id, store_id, campaign_id, data_date, rule_version,
                action, input_hash, revision, content_hash, payload_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute(
            """INSERT INTO optimizer_recommendations(
                recommendation_id, store_id, campaign_id, data_date, rule_version,
                action, input_hash, revision, content_hash, payload_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("rec-2", *values[1:]),
        )

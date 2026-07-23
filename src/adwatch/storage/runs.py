from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from adwatch.domain import ValidatedMetric
from adwatch.storage.db import Database


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    raise TypeError(f"cannot serialize {type(value).__name__}")


class RunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def start(self, mode: str, platform: str) -> str:
        run_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO collection_runs(
                    id, mode, platform, started_at, status
                ) VALUES (?, ?, ?, ?, 'running')
                """,
                (run_id, mode, platform, _utc_now()),
            )
        return run_id

    @staticmethod
    def store_quality(
        connection: sqlite3.Connection,
        run_id: str,
        valid: list[ValidatedMetric],
        invalid: list[ValidatedMetric],
    ) -> None:
        connection.execute(
            """
            INSERT INTO quality_checks(
                run_id, check_code, passed, affected_count, details_json
            ) VALUES (?, 'record_validation', ?, ?, ?)
            """,
            (
                run_id,
                int(not invalid),
                len(invalid),
                json.dumps(
                    {"accepted": len(valid), "quarantined": len(invalid)},
                    sort_keys=True,
                ),
            ),
        )
        connection.executemany(
            """
            INSERT INTO quarantined_records(run_id, raw_json, issues_json)
            VALUES (?, ?, ?)
            """,
            [
                (
                    run_id,
                    json.dumps(
                        asdict(result.metric),
                        default=_json_default,
                        sort_keys=True,
                    ),
                    json.dumps(
                        [asdict(issue) for issue in result.issues],
                        sort_keys=True,
                    ),
                )
                for result in invalid
            ],
        )

    def finish(
        self,
        run_id: str,
        *,
        received: int,
        accepted: int,
        quarantined: int,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE collection_runs
                SET finished_at = ?, status = 'succeeded',
                    received_count = ?, accepted_count = ?,
                    quarantined_count = ?
                WHERE id = ?
                """,
                (_utc_now(), received, accepted, quarantined, run_id),
            )

    def fail(self, run_id: str, error: Exception) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE collection_runs
                SET finished_at = ?, status = 'failed',
                    error_code = ?, error_message = ?
                WHERE id = ?
                """,
                (_utc_now(), type(error).__name__, str(error), run_id),
            )

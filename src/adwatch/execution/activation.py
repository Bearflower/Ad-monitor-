from __future__ import annotations

import json
from dataclasses import dataclass

from adwatch.storage.db import Database


@dataclass(frozen=True)
class SelectorActivation:
    platform: str
    action: str
    selector_version: str
    selectors: dict[str, str]
    store_id: str
    activated_by: str
    evidence_before: str
    evidence_after: str
    activated_at: str


class SelectorActivationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def register(
        self,
        *,
        platform: str,
        action: str,
        selector_version: str,
        selectors: dict[str, str],
        store_id: str,
        activated_by: str,
        evidence_before: str,
        evidence_after: str,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO selector_activations(
                    platform, action, selector_version, selectors_json,
                    store_id, activated_by, evidence_before, evidence_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, action) DO UPDATE SET
                    selector_version=excluded.selector_version,
                    selectors_json=excluded.selectors_json,
                    store_id=excluded.store_id,
                    activated_by=excluded.activated_by,
                    evidence_before=excluded.evidence_before,
                    evidence_after=excluded.evidence_after,
                    activated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    platform,
                    action,
                    selector_version,
                    json.dumps(selectors, ensure_ascii=False, sort_keys=True),
                    store_id,
                    activated_by,
                    evidence_before,
                    evidence_after,
                ),
            )

    def get(self, platform: str, action: str) -> SelectorActivation | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM selector_activations
                WHERE platform=? AND action=?
                """,
                (platform, action),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def list(self) -> tuple[SelectorActivation, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM selector_activations
                ORDER BY platform, action
                """
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row) -> SelectorActivation:
        return SelectorActivation(
            platform=row["platform"],
            action=row["action"],
            selector_version=row["selector_version"],
            selectors=json.loads(row["selectors_json"]),
            store_id=row["store_id"],
            activated_by=row["activated_by"],
            evidence_before=row["evidence_before"],
            evidence_after=row["evidence_after"],
            activated_at=row["activated_at"],
        )

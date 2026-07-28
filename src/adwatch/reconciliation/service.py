from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from adwatch.storage.db import Database


@dataclass(frozen=True)
class Difference:
    field: str
    expected: str
    actual: str
    category: str


@dataclass(frozen=True)
class ReconciliationDay:
    data_date: date
    accuracy: Decimal
    differences: tuple[Difference, ...]


def _json_value(value: object) -> object:
    return str(value) if isinstance(value, Decimal) else value


NUMERIC_TOLERANCE = Decimal("0.01")


def _matches(expected: object, actual: object, category: str) -> bool:
    if actual is None:
        return False
    if category in {"money", "ratio"}:
        try:
            difference = abs(
                Decimal(str(expected)) - Decimal(str(actual))
            )
            return difference <= NUMERIC_TOLERANCE
        except InvalidOperation:
            return False
    if category == "count":
        try:
            left = Decimal(str(expected))
            right = Decimal(str(actual))
        except InvalidOperation:
            return False
        return (
            left == left.to_integral_value()
            and right == right.to_integral_value()
            and left == right
        )
    return str(expected).strip() == str(actual).strip()


class ReconciliationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record_day(
        self,
        *,
        platform: str,
        store: str,
        data_date: date,
        expected: dict[str, object],
        actual: dict[str, object],
        difference_categories: dict[str, str] | None = None,
    ) -> ReconciliationDay:
        if not expected:
            raise ValueError("expected fields are required")
        categories = difference_categories or {}
        differences = tuple(
            Difference(
                field=field,
                expected=str(value),
                actual=str(actual.get(field, "<missing>")),
                category=categories.get(field, "unknown"),
            )
            for field, value in expected.items()
            if not _matches(
                value,
                actual.get(field),
                categories.get(field, "unknown"),
            )
        )
        matched = len(expected) - len(differences)
        accuracy = (Decimal(matched) / Decimal(len(expected))).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reconciliation_days(
                    platform, store, data_date, accuracy, expected_json,
                    actual_json, differences_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, store, data_date) DO UPDATE SET
                  accuracy=excluded.accuracy,
                  expected_json=excluded.expected_json,
                  actual_json=excluded.actual_json,
                  differences_json=excluded.differences_json,
                  created_at=excluded.created_at
                """,
                (
                    platform,
                    store,
                    data_date.isoformat(),
                    str(accuracy),
                    json.dumps(expected, default=_json_value, sort_keys=True),
                    json.dumps(actual, default=_json_value, sort_keys=True),
                    json.dumps(
                        [asdict(item) for item in differences], sort_keys=True
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return ReconciliationDay(data_date, accuracy, differences)

    def report(
        self,
        *,
        platform: str,
        store: str,
        start: date,
        end: date,
    ) -> tuple[ReconciliationDay, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reconciliation_days
                WHERE platform=? AND store=? AND data_date BETWEEN ? AND ?
                ORDER BY data_date
                """,
                (platform, store, start.isoformat(), end.isoformat()),
            ).fetchall()
        return tuple(
            ReconciliationDay(
                data_date=date.fromisoformat(row["data_date"]),
                accuracy=Decimal(row["accuracy"]),
                differences=tuple(
                    Difference(**item)
                    for item in json.loads(row["differences_json"])
                ),
            )
            for row in rows
        )

    def three_day_ready(
        self, *, platform: str, store: str, through: date
    ) -> bool:
        start = through - timedelta(days=2)
        rows = self.report(
            platform=platform, store=store, start=start, end=through
        )
        return (
            len(rows) == 3
            and tuple(item.data_date for item in rows)
            == tuple(start + timedelta(days=offset) for offset in range(3))
            and all(item.accuracy >= Decimal("0.99") for item in rows)
        )

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from adwatch.storage.db import Database
from adwatch.storage.store_aliases import equivalent_store_names


@dataclass(frozen=True)
class RefundClosureDay:
    platform: str
    store: str
    data_date: date
    status: str
    source_type: str
    source_ref: str


@dataclass(frozen=True)
class RefundClosureResult:
    examined: int = 0
    closed_zero: int = 0
    pending: int = 0
    unchanged: int = 0
    days: tuple[RefundClosureDay, ...] = ()


class RefundClosureService:
    """Close refund inputs only after a complete T+3 order-sync window."""

    def __init__(self, database: Database, *, today: date | None = None) -> None:
        self.database = database
        self.today = today or datetime.now(
            ZoneInfo("Asia/Shanghai")
        ).date()

    def close(
        self,
        *,
        start: date,
        end: date,
        _connection=None,
    ) -> RefundClosureResult:
        if start > end:
            raise ValueError("start date must not be after end date")
        now = datetime.now(UTC).isoformat()
        days: list[RefundClosureDay] = []
        counts = {"closed_zero": 0, "pending": 0, "unchanged": 0}
        transaction = (
            self.database.transaction()
            if _connection is None
            else nullcontext(_connection)
        )
        with transaction as connection:
            metrics = connection.execute(
                """
                SELECT DISTINCT platform, store, data_date, currency, source
                FROM daily_ad_metrics
                WHERE data_date BETWEEN ? AND ?
                ORDER BY data_date, platform, store
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            for metric in metrics:
                data_date = date.fromisoformat(metric["data_date"])
                existing = connection.execute(
                    """
                    SELECT * FROM daily_refund_inputs
                    WHERE platform=? AND store=? AND data_date=?
                    """,
                    (metric["platform"], metric["store"], metric["data_date"]),
                ).fetchone()
                if existing is not None and existing["status"] == "observed":
                    status = "observed"
                    source_type = existing["source_type"]
                    source_ref = existing["source_ref"]
                    counts["unchanged"] += 1
                    days.append(
                        RefundClosureDay(
                            metric["platform"],
                            metric["store"],
                            data_date,
                            status,
                            source_type,
                            source_ref,
                        )
                    )
                    continue

                stores = equivalent_store_names(
                    connection,
                    platform=metric["platform"],
                    store=metric["store"],
                )
                store_placeholders = ", ".join("?" for _ in stores)
                observations = connection.execute(
                    f"""
                    SELECT refund_status, amount_original, currency,
                           observed_on, observed_at, source_updated_at
                    FROM (
                        SELECT refund_status, amount_original, currency,
                               observed_on, observed_at, source_updated_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY platform, order_id
                                   ORDER BY observed_at DESC,
                                            source_updated_at DESC,
                                            observed_on DESC,
                                            store DESC
                               ) AS row_number
                        FROM platform_refund_observations
                        WHERE platform=? AND store IN ({store_placeholders})
                          AND order_date=?
                    )
                    WHERE row_number=1
                    ORDER BY observed_on
                    """,
                    (
                        metric["platform"],
                        *stores,
                        metric["data_date"],
                    ),
                ).fetchall()
                statuses = [
                    str(row["refund_status"] or "").strip()
                    for row in observations
                    if str(row["refund_status"] or "").strip()
                ]
                amounts = [row["amount_original"] for row in observations]
                if (
                    existing is None
                    and not statuses
                    and metric["source"] == "mock"
                    and not self._has_any_sync(
                        connection,
                        platform=metric["platform"],
                        store=metric["store"],
                    )
                ):
                    counts["unchanged"] += 1
                    days.append(
                        RefundClosureDay(
                            metric["platform"],
                            metric["store"],
                            data_date,
                            "legacy_unmanaged",
                            "legacy",
                            "no_order_sync_history",
                        )
                    )
                    continue
                if (
                    existing is not None
                    and existing["status"] == "t3_closed_zero"
                    and not statuses
                ):
                    counts["unchanged"] += 1
                    days.append(
                        RefundClosureDay(
                            metric["platform"],
                            metric["store"],
                            data_date,
                            existing["status"],
                            existing["source_type"],
                            existing["source_ref"],
                        )
                    )
                    continue
                eligible_on = data_date + timedelta(days=3)
                covered = self._has_successful_coverage(
                    connection,
                    platform=metric["platform"],
                    store=metric["store"],
                    data_date=data_date,
                    covered_through=eligible_on,
                )

                if statuses and any(amount is None for amount in amounts):
                    status = "pending_data"
                    source_type = "platform_refund_observations"
                    source_ref = "refund_amount_missing"
                    amount = "0"
                    currency = metric["currency"]
                    observed_on = None
                    counts["pending"] += 1
                elif statuses:
                    currencies = {
                        str(row["currency"] or "").strip().upper()
                        for row in observations
                    }
                    if len(currencies) != 1:
                        status = "pending_data"
                        source_type = "platform_refund_observations"
                        source_ref = "refund_currency_ambiguous"
                        amount = "0"
                        currency = metric["currency"]
                        observed_on = None
                        counts["pending"] += 1
                    elif next(iter(currencies)) != str(
                        metric["currency"] or ""
                    ).strip().upper():
                        status = "pending_data"
                        source_type = "platform_refund_observations"
                        source_ref = "refund_currency_mismatch"
                        amount = "0"
                        currency = metric["currency"]
                        observed_on = None
                        counts["pending"] += 1
                    else:
                        total = sum(
                            (Decimal(str(row["amount_original"])) for row in observations),
                            Decimal(0),
                        )
                        status = "observed"
                        source_type = "platform_refund_observations"
                        source_ref = "observed_refund_amount"
                        amount = str(total)
                        currency = next(iter(currencies))
                        observed_on = max(row["observed_on"] for row in observations)
                        counts["unchanged"] += 1
                elif self.today < eligible_on:
                    status = "pending_data"
                    source_type = "refund_closure"
                    source_ref = "t3_not_reached"
                    amount = "0"
                    currency = metric["currency"]
                    observed_on = None
                    counts["pending"] += 1
                elif not covered:
                    status = "pending_data"
                    source_type = "refund_closure"
                    source_ref = "sync_coverage_missing"
                    amount = "0"
                    currency = metric["currency"]
                    observed_on = None
                    counts["pending"] += 1
                else:
                    status = "t3_closed_zero"
                    source_type = "t3_no_refund_observed"
                    source_ref = "t3_no_refund_observed"
                    amount = "0"
                    currency = metric["currency"]
                    observed_on = None
                    counts["closed_zero"] += 1

                connection.execute(
                    """
                    INSERT INTO daily_refund_inputs(
                        platform, store, data_date, refund_amount_original,
                        currency, status, source_type, source_ref,
                        observed_on, closed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, store, data_date) DO UPDATE SET
                        refund_amount_original=excluded.refund_amount_original,
                        currency=excluded.currency,
                        status=excluded.status,
                        source_type=excluded.source_type,
                        source_ref=excluded.source_ref,
                        observed_on=excluded.observed_on,
                        closed_at=excluded.closed_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        metric["platform"],
                        metric["store"],
                        metric["data_date"],
                        amount,
                        currency,
                        status,
                        source_type,
                        source_ref,
                        observed_on,
                        now if status == "t3_closed_zero" else None,
                        now,
                    ),
                )
                days.append(
                    RefundClosureDay(
                        metric["platform"],
                        metric["store"],
                        data_date,
                        status,
                        source_type,
                        source_ref,
                    )
                )
        return RefundClosureResult(
            examined=len(days),
            closed_zero=counts["closed_zero"],
            pending=counts["pending"],
            unchanged=counts["unchanged"],
            days=tuple(days),
        )

    @staticmethod
    def _has_successful_coverage(
        connection,
        *,
        platform: str,
        store: str,
        data_date: date,
        covered_through: date,
    ) -> bool:
        stores = equivalent_store_names(
            connection, platform=platform, store=store
        )
        placeholders = ", ".join("?" for _ in stores)
        row = connection.execute(
            f"""
            SELECT 1
            FROM order_sync_runs
            WHERE platform=? AND store IN ({placeholders})
              AND status='success'
              AND start_date IS NOT NULL AND end_date IS NOT NULL
              AND start_date <= ? AND end_date >= ?
            LIMIT 1
            """,
            (
                platform,
                *stores,
                data_date.isoformat(),
                covered_through.isoformat(),
            ),
        ).fetchone()
        return row is not None

    @staticmethod
    def _has_any_sync(connection, *, platform: str, store: str) -> bool:
        stores = equivalent_store_names(
            connection, platform=platform, store=store
        )
        placeholders = ", ".join("?" for _ in stores)
        return (
            connection.execute(
                f"""
                SELECT 1 FROM order_sync_runs
                WHERE platform=? AND store IN ({placeholders})
                LIMIT 1
                """,
                (platform, *stores),
            ).fetchone()
            is not None
        )

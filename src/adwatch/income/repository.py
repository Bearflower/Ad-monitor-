from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.income.models import FeeStatement, ResolvedFeeRate
from adwatch.storage.db import Database


class FeeStatementRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, statement: FeeStatement) -> str:
        with self.database.transaction() as connection:
            return self.save_with_connection(connection, statement)

    def has_processed_source(self, source_sha256: str) -> bool:
        return self.processed_status(source_sha256) is not None

    def processed_status(self, source_sha256: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT status FROM income_import_events WHERE source_sha256=?
                """,
                (source_sha256,),
            ).fetchone()
        return None if row is None else str(row["status"])

    def canonical_store(self, platform: str, source_store: str) -> str | None:
        with self.database.connect() as connection:
            alias = connection.execute(
                """
                SELECT canonical_store FROM store_aliases
                WHERE platform=? AND source_store=?
                """,
                (platform, source_store),
            ).fetchone()
            if alias is not None:
                return str(alias["canonical_store"])
            direct = connection.execute(
                "SELECT store FROM stores WHERE platform=? AND store=?",
                (platform, source_store),
            ).fetchone()
        return None if direct is None else str(direct["store"])

    def record_event(
        self,
        *,
        source_sha256: str,
        source_name: str,
        platform: str,
        store: str,
        status: str,
        reason_code: str,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO income_import_events(
                    source_sha256, source_name, platform, store, status,
                    reason_code, statement_id, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(source_sha256) DO UPDATE SET
                    source_name=excluded.source_name,
                    platform=excluded.platform,
                    store=excluded.store,
                    status=excluded.status,
                    reason_code=excluded.reason_code,
                    statement_id=NULL,
                    occurred_at=excluded.occurred_at
                WHERE income_import_events.status IN (
                    'quarantined', 'failed'
                )
                """,
                (
                    source_sha256,
                    source_name,
                    platform,
                    store,
                    status,
                    reason_code,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def save_with_connection(self, connection, statement: FeeStatement) -> str:
        duplicate = connection.execute(
            """
            SELECT id FROM platform_fee_statements
            WHERE source_sha256=?
            """,
            (statement.source_sha256,),
        ).fetchone()
        if duplicate is not None:
            return "skipped"

        current = connection.execute(
            """
            SELECT id, version
            FROM platform_fee_statements
            WHERE platform=? AND store=? AND period_start=? AND period_end=?
              AND is_current=1
            """,
            (
                statement.platform,
                statement.store,
                statement.period_start.isoformat(),
                statement.period_end.isoformat(),
            ),
        ).fetchone()
        status = "revised" if current is not None else "imported"
        version = int(current["version"]) + 1 if current is not None else 1
        if current is not None:
            connection.execute(
                "UPDATE platform_fee_statements SET is_current=0 WHERE id=?",
                (current["id"],),
            )

        statement_id = str(uuid.uuid4())
        occurred_at = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO platform_fee_statements(
                id, platform, store, source_store, period_start, period_end,
                currency, product_price, commission_fee, service_fee,
                transaction_fee, affiliate_fee, sales_fee_total,
                effective_rate, seller_voucher, shipping_paid_by_buyer,
                shipping_provider_charge, shipping_rebate, ads_credit_topup,
                adjustment_amount, total_payout, source_name, source_sha256,
                version, is_current, imported_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                statement_id,
                statement.platform,
                statement.store,
                statement.source_store,
                statement.period_start.isoformat(),
                statement.period_end.isoformat(),
                statement.currency,
                str(statement.product_price),
                str(statement.commission_fee),
                str(statement.service_fee),
                str(statement.transaction_fee),
                str(statement.affiliate_fee),
                str(statement.sales_fee_total),
                str(statement.effective_rate),
                str(statement.seller_voucher),
                str(statement.shipping_paid_by_buyer),
                str(statement.shipping_provider_charge),
                str(statement.shipping_rebate),
                str(statement.ads_credit_topup),
                str(statement.adjustment_amount),
                str(statement.total_payout),
                statement.source_name,
                statement.source_sha256,
                version,
                1,
                occurred_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO income_import_events(
                source_sha256, source_name, platform, store, status,
                reason_code, statement_id, occurred_at
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                statement.source_sha256,
                statement.source_name,
                statement.platform,
                statement.store,
                status,
                statement_id,
                occurred_at,
            ),
        )
        return status

    def resolve(
        self,
        platform: str,
        store: str,
        data_date: date,
    ) -> ResolvedFeeRate | None:
        day = data_date.isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT effective_rate, period_start, period_end,
                       source_name, source_sha256
                FROM platform_fee_statements
                WHERE platform=? AND store=? AND is_current=1
                  AND period_start<=?
                ORDER BY
                  CASE WHEN period_end>=? THEN 0 ELSE 1 END,
                  period_end DESC,
                  version DESC
                LIMIT 1
                """,
                (platform, store, day, day),
            ).fetchone()
        if row is None:
            return None
        period_end = date.fromisoformat(row["period_end"])
        return ResolvedFeeRate(
            rate=Decimal(row["effective_rate"]),
            period_start=date.fromisoformat(row["period_start"]),
            period_end=period_end,
            selection=(
                "covering_period"
                if data_date <= period_end
                else "carried_forward"
            ),
            source_name=row["source_name"],
            source_sha256=row["source_sha256"],
        )

    def list_current(self) -> tuple[dict[str, object], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT platform, store, period_start, period_end,
                       effective_rate, source_name, source_sha256, version
                FROM platform_fee_statements
                WHERE is_current=1
                ORDER BY platform, store, period_start, period_end
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)

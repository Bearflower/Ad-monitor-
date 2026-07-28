from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from adwatch.storage.db import Database


class ProfitSharingError(ValueError):
    pass


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ProfitSharingService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_agreement(
        self,
        *,
        effective_from: date,
        shares: dict[str, Decimal],
        actor: str,
    ) -> str:
        if not shares or sum(shares.values(), Decimal(0)) != Decimal(1):
            raise ProfitSharingError("share ratios must total one")
        if any(value <= 0 for value in shares.values()):
            raise ProfitSharingError("share ratios must be positive")
        agreement_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            latest = connection.execute(
                """
                SELECT id, effective_from, version
                FROM profit_share_agreements
                ORDER BY effective_from DESC LIMIT 1
                """
            ).fetchone()
            if latest and effective_from.isoformat() <= latest["effective_from"]:
                raise ProfitSharingError(
                    "new agreement must start after the latest agreement"
                )
            version = 1 if latest is None else int(latest["version"]) + 1
            if latest:
                connection.execute(
                    "UPDATE profit_share_agreements SET effective_to=? WHERE id=?",
                    (
                        date.fromordinal(effective_from.toordinal() - 1).isoformat(),
                        latest["id"],
                    ),
                )
            connection.execute(
                """
                INSERT INTO profit_share_agreements(
                    id, effective_from, version, shares_json,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    agreement_id,
                    effective_from.isoformat(),
                    version,
                    json.dumps(
                        {key: str(value) for key, value in shares.items()},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    actor,
                    now,
                ),
            )
        return agreement_id

    def create_period(
        self,
        *,
        starts_on: date,
        ends_on: date,
        net_profit_cny: Decimal,
        actor: str,
    ) -> str:
        if starts_on > ends_on:
            raise ProfitSharingError("period start must not exceed end")
        with self.database.transaction() as connection:
            agreement = connection.execute(
                """
                SELECT * FROM profit_share_agreements
                WHERE effective_from <= ?
                  AND (effective_to IS NULL OR effective_to >= ?)
                ORDER BY effective_from DESC LIMIT 1
                """,
                (starts_on.isoformat(), starts_on.isoformat()),
            ).fetchone()
            if agreement is None:
                raise ProfitSharingError("no effective profit agreement")
            period_id = str(uuid.uuid4())
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO profit_periods(
                    id, starts_on, ends_on, agreement_id, net_profit_cny,
                    status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    period_id,
                    starts_on.isoformat(),
                    ends_on.isoformat(),
                    agreement["id"],
                    str(_money(net_profit_cny)),
                    actor,
                    now,
                ),
            )
            shares = json.loads(agreement["shares_json"])
            for partner, ratio_text in shares.items():
                ratio = Decimal(ratio_text)
                connection.execute(
                    """
                    INSERT INTO profit_allocations(
                        period_id, partner, share_ratio, amount_cny
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        period_id,
                        partner,
                        str(ratio),
                        str(_money(net_profit_cny * ratio)),
                    ),
                )
        return period_id

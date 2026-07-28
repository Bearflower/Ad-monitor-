from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from adwatch.storage.db import Database
from adwatch.strategy.rules import StrategyContext, recommend


@dataclass(frozen=True)
class ReplayResult:
    status: str
    checked: int
    mismatches: tuple[str, ...]


_DECIMALS = {
    "roas",
    "target_roas",
    "net_profit",
    "inventory_cover_days",
    "current_budget",
    "baseline_budget",
    "available_test_budget",
    "net_sales_roas",
    "profit_roas",
    "refund_rate",
}


class StrategyReplayService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def replay(
        self,
        *,
        platform: str,
        store: str,
        campaign_id: str,
        start: date,
        end: date,
    ) -> ReplayResult:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM recommendations
                WHERE platform=? AND store_id=? AND campaign_id=?
                  AND data_date BETWEEN ? AND ?
                ORDER BY data_date, id
                """,
                (
                    platform,
                    store,
                    campaign_id,
                    start.isoformat(),
                    end.isoformat(),
                ),
            ).fetchall()
        if not rows:
            return ReplayResult("pending_data", 0, ())
        mismatches = []
        for row in rows:
            evidence = json.loads(row["evidence_json"])
            raw = evidence.get("strategy_context")
            if not isinstance(raw, dict) or not row["rule_version_id"]:
                mismatches.append(f"{row['id']}:missing_context")
                continue
            values = dict(raw)
            values["campaign_start"] = date.fromisoformat(
                values["campaign_start"]
            )
            values["data_date"] = date.fromisoformat(values["data_date"])
            for key in _DECIMALS:
                if values.get(key) is not None:
                    values[key] = Decimal(values[key])
            context = StrategyContext(**values)
            replayed = {
                (item.rule_code, item.action) for item in recommend(context)
            }
            if (row["rule_code"], row["action"]) not in replayed:
                mismatches.append(f"{row['id']}:action_mismatch")
        result = ReplayResult(
            "matched" if not mismatches else "mismatched",
            len(rows),
            tuple(mismatches),
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO strategy_replays(
                    id, platform, store, campaign_id, starts_on, ends_on,
                    rule_version_id, matches_original, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    platform,
                    store,
                    campaign_id,
                    start.isoformat(),
                    end.isoformat(),
                    rows[0]["rule_version_id"],
                    int(not mismatches),
                    json.dumps({"mismatches": mismatches}, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return result

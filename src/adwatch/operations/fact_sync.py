from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from adwatch.storage.db import Database

BUNDLE_VERSION = 1

_RANGE_TABLES = {
    "daily_ad_metrics": ("data_date", "date"),
    "platform_order_lines": ("ordered_at", "datetime"),
    "platform_refund_observations": ("order_date", "date"),
    "platform_logistics_observations": ("order_date", "date"),
    "platform_inventory_snapshots": ("snapshot_date", "date"),
    "exchange_rates": ("rate_date", "date"),
    "daily_refund_inputs": ("data_date", "date"),
}
_REFERENCE_TABLES = (
    "order_sync_runs",
    "platform_sku_mappings",
    "store_aliases",
    "sku_cost_history",
    "sku_fulfillment_history",
    "store_fulfillment_history",
    "product_costs",
)
_FILL_MISSING_TABLES = frozenset(
    {
        "store_aliases",
        "sku_cost_history",
        "sku_fulfillment_history",
        "store_fulfillment_history",
        "product_costs",
        "daily_refund_inputs",
    }
)
_SAFE_TARGET = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+$")
_SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class FactImportResult:
    start: date
    end: date
    rows: int
    tables: dict[str, int]


def export_facts(database: Database, *, start: date, end: date) -> bytes:
    if start > end:
        raise ValueError("start date must not be after end date")
    tables: dict[str, list[dict[str, Any]]] = {}
    with database.connect() as connection:
        for table, (column, kind) in _RANGE_TABLES.items():
            expression = f"substr({column}, 1, 10)" if kind == "datetime" else column
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE {expression} BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            tables[table] = [_portable_row(table, dict(row)) for row in rows]
        for table in _REFERENCE_TABLES:
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            tables[table] = [dict(row) for row in rows]
    payload = {
        "version": BUNDLE_VERSION,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "tables": tables,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def import_facts(database: Database, payload: bytes) -> FactImportResult:
    try:
        bundle = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("fact bundle is not valid JSON") from error
    if not isinstance(bundle, dict) or bundle.get("version") != BUNDLE_VERSION:
        raise ValueError("unsupported fact bundle version")
    start = date.fromisoformat(str(bundle.get("start", "")))
    end = date.fromisoformat(str(bundle.get("end", "")))
    if start > end:
        raise ValueError("fact bundle start date must not be after end date")
    raw_tables = bundle.get("tables")
    if not isinstance(raw_tables, dict):
        raise TypeError("fact bundle tables are missing")
    allowed = set(_RANGE_TABLES) | set(_REFERENCE_TABLES)
    unknown = set(raw_tables) - allowed
    if unknown:
        raise ValueError(f"fact bundle contains unsupported tables: {sorted(unknown)}")

    counts: dict[str, int] = {}
    with database.transaction() as connection:
        metric_rows = _validated_rows(raw_tables, "daily_ad_metrics")
        metric_scopes = {
            (str(row["platform"]), str(row["data_date"])) for row in metric_rows
        }
        for platform, data_date in metric_scopes:
            connection.execute(
                "DELETE FROM daily_ad_metrics WHERE platform=? AND data_date=?",
                (platform, data_date),
            )
        for table in (*_RANGE_TABLES, *_REFERENCE_TABLES):
            rows = _validated_rows(raw_tables, table)
            counts[table] = _insert_rows(
                connection,
                table,
                rows,
                fill_missing=table in _FILL_MISSING_TABLES,
            )
    return FactImportResult(
        start=start,
        end=end,
        rows=sum(counts.values()),
        tables=counts,
    )


def publish_facts(
    payload: bytes,
    *,
    target: str,
    container: str = "adwatch",
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    if not _SAFE_TARGET.fullmatch(target):
        raise ValueError("publish SSH target is invalid")
    if not _SAFE_CONTAINER.fullmatch(container):
        raise ValueError("publish container name is invalid")
    completed = runner(
        [
            "ssh",
            target,
            "docker",
            "exec",
            "-i",
            container,
            "adwatch",
            "replicate",
            "import",
            "--stdin",
        ],
        input=payload,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or stdout or "remote fact import failed")
    return stdout


def export_facts_to_path(
    database: Database, *, start: date, end: date, destination: Path
) -> Path:
    payload = export_facts(database, start=start, end=end)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


def _portable_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    if table == "daily_ad_metrics":
        row.pop("id", None)
    return row


def _validated_rows(raw_tables: dict[str, Any], table: str) -> list[dict[str, Any]]:
    raw = raw_tables.get(table, [])
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise ValueError(f"fact bundle table {table} is invalid")
    return raw


def _insert_rows(connection, table: str, rows: list[dict[str, Any]], *, fill_missing: bool) -> int:
    if not rows:
        return 0
    valid_columns = {
        str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
    }
    columns = tuple(rows[0])
    if not columns or not set(columns) <= valid_columns:
        raise ValueError(f"fact bundle columns for {table} are invalid")
    if any(tuple(row) != columns for row in rows):
        raise ValueError(f"fact bundle rows for {table} have inconsistent columns")
    placeholders = ",".join("?" for _ in columns)
    verb = "INSERT OR IGNORE" if fill_missing else "INSERT OR REPLACE"
    connection.executemany(
        f"{verb} INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )
    return len(rows)

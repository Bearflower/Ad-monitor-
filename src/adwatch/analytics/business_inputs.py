from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from adwatch.storage.db import Database


class BusinessInputError(ValueError):
    pass


COLUMNS = (
    "data_date",
    "platform",
    "store",
    "campaign_id",
    "sku_id",
    "currency",
    "product_cost",
    "commission_rate",
    "seller_shipping",
    "coupons",
    "allocated_fixed_cost",
    "refund_amount",
    "inventory_units",
    "expected_daily_units",
    "rate_to_cny",
    "start_date",
    "target_roas",
    "current_budget",
    "baseline_budget",
)
INPUT_COLUMNS = COLUMNS[6:]


def export_business_template(
    database: Database,
    *,
    start: date,
    end: date,
    destination: Path,
) -> int:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT data_date, platform, store, campaign_id, sku_id, currency
            FROM daily_ad_metrics
            WHERE data_date BETWEEN ? AND ?
            ORDER BY data_date, platform, campaign_id, sku_id
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **{key: row[key] for key in COLUMNS[:6]},
                    **{key: "" for key in INPUT_COLUMNS},
                }
            )
    return len(rows)


def import_business_inputs(database: Database, source: Path) -> int:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing_columns = [key for key in COLUMNS if key not in reader.fieldnames]
        if missing_columns:
            raise BusinessInputError(
                f"missing columns: {', '.join(missing_columns)}"
            )
        rows = list(reader)
    validated = [_validate_row(row, index + 2) for index, row in enumerate(rows)]
    with database.transaction() as connection:
        for row in validated:
            connection.execute(
                """
                INSERT INTO stores(platform, store, country, currency)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(platform, store) DO UPDATE SET
                    country=excluded.country, currency=excluded.currency
                """,
                (
                    row["platform"],
                    row["store"],
                    "TH" if row["currency"] == "THB" else "",
                    row["currency"],
                ),
            )
            connection.execute(
                """
                INSERT INTO campaign_settings(
                    platform, campaign_id, start_date, target_roas,
                    current_budget, baseline_budget
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, campaign_id) DO UPDATE SET
                    start_date=excluded.start_date,
                    target_roas=excluded.target_roas,
                    current_budget=excluded.current_budget,
                    baseline_budget=excluded.baseline_budget
                """,
                (
                    row["platform"],
                    row["campaign_id"],
                    row["start_date"],
                    row["target_roas"],
                    row["current_budget"],
                    row["baseline_budget"],
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO sku_mappings(sku_id) VALUES (?)",
                (row["sku_id"],),
            )
            connection.execute(
                """
                INSERT INTO product_costs(
                    sku_id, effective_date, product_cost, commission_rate,
                    seller_shipping, coupons, allocated_fixed_cost,
                    refund_amount
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sku_id, effective_date) DO UPDATE SET
                    product_cost=excluded.product_cost,
                    commission_rate=excluded.commission_rate,
                    seller_shipping=excluded.seller_shipping,
                    coupons=excluded.coupons,
                    allocated_fixed_cost=excluded.allocated_fixed_cost,
                    refund_amount=excluded.refund_amount
                """,
                (
                    row["sku_id"],
                    row["data_date"],
                    row["product_cost"],
                    row["commission_rate"],
                    row["seller_shipping"],
                    row["coupons"],
                    row["allocated_fixed_cost"],
                    row["refund_amount"],
                ),
            )
            connection.execute(
                """
                INSERT INTO inventory_snapshots(
                    sku_id, snapshot_date, units, expected_daily_units
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(sku_id, snapshot_date) DO UPDATE SET
                    units=excluded.units,
                    expected_daily_units=excluded.expected_daily_units
                """,
                (
                    row["sku_id"],
                    row["data_date"],
                    row["inventory_units"],
                    row["expected_daily_units"],
                ),
            )
            connection.execute(
                """
                INSERT INTO exchange_rates(currency, rate_date, rate_to_cny)
                VALUES (?, ?, ?)
                ON CONFLICT(currency, rate_date) DO UPDATE SET
                    rate_to_cny=excluded.rate_to_cny
                """,
                (row["currency"], row["data_date"], row["rate_to_cny"]),
            )
    return len(validated)


def _validate_row(row: dict[str, str], line: int) -> dict[str, str]:
    missing = [key for key in COLUMNS if not (row.get(key) or "").strip()]
    if missing:
        raise BusinessInputError(
            f"line {line} missing values: {', '.join(missing)}"
        )
    try:
        date.fromisoformat(row["data_date"])
        date.fromisoformat(row["start_date"])
        decimals = {
            key: Decimal(row[key])
            for key in (
                "product_cost",
                "commission_rate",
                "seller_shipping",
                "coupons",
                "allocated_fixed_cost",
                "refund_amount",
                "expected_daily_units",
                "rate_to_cny",
                "target_roas",
                "current_budget",
                "baseline_budget",
            )
        }
        units = int(row["inventory_units"])
    except (ValueError, InvalidOperation) as error:
        raise BusinessInputError(f"line {line} has invalid values") from error
    if any(value < 0 for value in decimals.values()) or units < 0:
        raise BusinessInputError(f"line {line} contains negative values")
    if decimals["commission_rate"] > 1:
        raise BusinessInputError(
            f"line {line} commission_rate must be between 0 and 1"
        )
    if decimals["rate_to_cny"] <= 0:
        raise BusinessInputError(f"line {line} rate_to_cny must be positive")
    return {key: row[key].strip() for key in COLUMNS}

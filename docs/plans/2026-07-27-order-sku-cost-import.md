# Order SKU Cost Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import Chinese-header Shopee/TikTok order SKU costs from XLSX or CSV into SQLite, map platform store names to collected store names, and safely use CNY costs in daily profit analysis.

**Architecture:** A focused `order_costs` module owns parsing, validation, transactional upsert, store aliases, and summaries. Migration 8 stores source order lines and explicit store aliases. Analytics aggregates order costs only when a platform/store/date has exactly one ad metric row; otherwise it records an ambiguity alert and never duplicates cost.

**Tech Stack:** Python 3.11, SQLite, `Decimal`, `openpyxl>=3.1,<4`, argparse, pytest, ruff.

---

## File map

- Create `src/adwatch/analytics/order_costs.py`: parse CSV/XLSX, validate rows, import idempotently, map stores, and query summaries.
- Modify `src/adwatch/storage/migrations.py`: migration 8 for `order_cost_lines` and `store_aliases`.
- Modify `src/adwatch/analytics/profit.py`: accept already-CNY product cost without converting it again.
- Modify `src/adwatch/storage/analytics.py`: aggregate mapped order costs and expose allocation ambiguity.
- Modify `src/adwatch/analytics/service.py`: use CNY order cost and create ambiguity alerts.
- Modify `src/adwatch/operations/launch_checklist.py`: no structural change; readiness query changes in CLI.
- Modify `src/adwatch/cli.py`: `import-orders`, `map-store`, and `order-summary`.
- Modify `pyproject.toml`: runtime XLSX dependency.
- Modify `README.md`: Chinese order-cost workflow and store mapping.
- Create `tests/analytics/test_order_costs.py`: parser, validation, transaction, idempotency, aliases, and summaries.
- Modify `tests/storage/test_analytics_migration.py`: migration 8 schema.
- Modify `tests/analytics/test_profit.py`: CNY cost behavior.
- Modify `tests/analytics/test_service.py`: safe allocation and ambiguity behavior.
- Modify `tests/operations/test_launch_checklist.py`: order costs satisfy `business_costs`.
- Modify `tests/test_business_cli.py`: new CLI commands and output.

### Task 1: Persist order cost lines and store aliases

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Modify: `tests/storage/test_analytics_migration.py`

- [ ] **Step 1: Write the failing migration test**

Add:

```python
def test_migration_creates_order_cost_lines_and_store_aliases(tmp_path):
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        order_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(order_cost_lines)")
        }

    assert {"order_cost_lines", "store_aliases"} <= tables
    assert {
        "platform", "store", "order_id", "sku_id", "order_date",
        "quantity", "unit_cost_cny", "line_cost_cny", "source_file",
        "updated_at",
    } <= order_columns
```

- [ ] **Step 2: Run the migration test and verify failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/storage/test_analytics_migration.py::test_migration_creates_order_cost_lines_and_store_aliases \
  -q
```

Expected: FAIL because migration 8 and the two tables do not exist.

- [ ] **Step 3: Add migration 8**

Append to `MIGRATIONS`:

```python
(
    8,
    """
    CREATE TABLE order_cost_lines (
        platform TEXT NOT NULL,
        store TEXT NOT NULL,
        order_id TEXT NOT NULL,
        sku_id TEXT NOT NULL,
        order_date TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        unit_cost_cny TEXT NOT NULL,
        line_cost_cny TEXT NOT NULL,
        source_file TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (
            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        ),
        PRIMARY KEY(platform, store, order_id, sku_id)
    );

    CREATE INDEX order_cost_lines_daily_idx
    ON order_cost_lines(platform, store, order_date);

    CREATE TABLE store_aliases (
        platform TEXT NOT NULL,
        source_store TEXT NOT NULL,
        canonical_store TEXT NOT NULL,
        PRIMARY KEY(platform, source_store)
    );
    """,
),
```

- [ ] **Step 4: Run storage tests**

Run:

```bash
.venv/bin/python -m pytest tests/storage -q
```

Expected: all storage tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adwatch/storage/migrations.py \
  tests/storage/test_analytics_migration.py
git commit -m "feat: store order SKU costs and aliases"
```

### Task 2: Parse and atomically import Chinese CSV/XLSX

**Files:**
- Create: `src/adwatch/analytics/order_costs.py`
- Create: `tests/analytics/test_order_costs.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing parser and import tests**

Create tests that build a workbook with `openpyxl.Workbook`, save it under
`tmp_path`, and call `import_order_costs`:

```python
HEADERS = ("日期", "平台", "店铺", "订单号", "SKU", "数量", "单件成本_人民币")


def test_import_xlsx_normalizes_dates_and_is_idempotent(tmp_path):
    source = tmp_path / "orders.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    sheet.append([20260708, "Shopee", "no4kud44da", "001", "1 bag", 1, 5])
    sheet.append(["2026-07-08", "shopee", "no4kud44da", "002", "3 bags", 2, 11])
    workbook.save(source)

    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    first = import_order_costs(database, source)
    second = import_order_costs(database, source)

    assert first.read == 2
    assert first.inserted == 2
    assert first.updated == 0
    assert first.total_cost_cny == Decimal("27.00")
    assert second.inserted == 0
    assert second.updated == 2
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_cost_lines"
        ).fetchone()[0] == 2
```

Add these CSV helpers and tests:

```python
def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def test_import_csv_allows_same_order_with_multiple_skus(tmp_path):
    source = tmp_path / "orders.csv"
    _write_csv(
        source,
        [
            [20260708, "shopee", "s", "o", "1 bag", 1, 5],
            [20260708, "shopee", "s", "o", "3 bags", 1, 11],
        ],
    )
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    summary = import_order_costs(database, source)
    assert summary.inserted == 2
    assert summary.total_cost_cny == Decimal("16.00")


def test_identical_file_duplicates_are_folded(tmp_path):
    source = tmp_path / "orders.csv"
    row = [20260708, "shopee", "s", "o", "1 bag", 1, 5]
    _write_csv(source, [row, row])
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    summary = import_order_costs(database, source)
    assert summary.read == 2
    assert summary.inserted == 1
    assert summary.deduplicated == 1


def test_conflicting_file_duplicates_reject_whole_batch(tmp_path):
    source = tmp_path / "orders.csv"
    _write_csv(
        source,
        [
            [20260708, "shopee", "s", "o", "1 bag", 1, 5],
            [20260708, "shopee", "s", "o", "1 bag", 2, 5],
        ],
    )
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    with pytest.raises(BusinessInputError, match="conflicting duplicate"):
        import_order_costs(database, source)
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_cost_lines"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "row",
    [
        ["", "shopee", "s", "o", "sku", 1, 5],
        [20260708, "amazon", "s", "o", "sku", 1, 5],
        [20260708, "shopee", "s", "o", "sku", 0, 5],
        [20260708, "shopee", "s", "o", "sku", 1, -1],
    ],
)
def test_invalid_rows_reject_whole_batch(tmp_path, row):
    source = tmp_path / "orders.csv"
    _write_csv(source, [row])
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    with pytest.raises(BusinessInputError):
        import_order_costs(database, source)
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_cost_lines"
        ).fetchone()[0] == 0
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/analytics/test_order_costs.py -q
```

Expected: FAIL because `adwatch.analytics.order_costs` does not exist.

- [ ] **Step 3: Add the XLSX runtime dependency**

Set:

```toml
[project]
dependencies = [
  "openpyxl>=3.1,<4",
]
```

Install the editable project:

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

Expected: `openpyxl` and the editable `adwatch` package install successfully.

- [ ] **Step 4: Implement the parser types and readers**

Create:

```python
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook

from adwatch.analytics.business_inputs import BusinessInputError
from adwatch.storage.db import Database

HEADERS = ("日期", "平台", "店铺", "订单号", "SKU", "数量", "单件成本_人民币")
PLATFORMS = {"shopee", "tiktok"}


@dataclass(frozen=True)
class OrderCostLine:
    platform: str
    store: str
    order_id: str
    sku_id: str
    order_date: date
    quantity: int
    unit_cost_cny: Decimal

    @property
    def line_cost_cny(self) -> Decimal:
        return self.unit_cost_cny * self.quantity

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.platform, self.store, self.order_id, self.sku_id


@dataclass(frozen=True)
class OrderImportSummary:
    read: int
    inserted: int
    updated: int
    deduplicated: int
    start: date
    end: date
    total_cost_cny: Decimal


def _raw_rows(path: Path) -> list[tuple[int, dict[str, object]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            _require_headers(reader.fieldnames)
            return [(line, dict(row)) for line, row in enumerate(reader, 2)]
    if suffix == ".xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                values = sheet.iter_rows(values_only=True)
                header = next(values, None)
                if header is None:
                    continue
                names = tuple("" if value is None else str(value).strip() for value in header)
                _require_headers(names)
                return [
                    (line, dict(zip(names, row, strict=False)))
                    for line, row in enumerate(values, 2)
                    if any(value not in (None, "") for value in row)
                ]
        finally:
            workbook.close()
        raise BusinessInputError("workbook has no non-empty worksheet")
    raise BusinessInputError("file must be .csv or .xlsx")


def _require_headers(fieldnames: Iterable[str] | None) -> None:
    names = tuple(fieldnames or ())
    missing = [name for name in HEADERS if name not in names]
    if missing:
        raise BusinessInputError(f"missing columns: {', '.join(missing)}")
```

Implement `_parse_date`, `_parse_line`, and a four-decimal precision check. Accept
numeric/string `YYYYMMDD`, ISO strings, `date`, and `datetime`; reject booleans,
fractional quantities, unsupported platforms, blanks, negative cost, and costs
with exponent below `-4`.

- [ ] **Step 5: Implement deduplication and transactional upsert**

Implement:

```python
def import_order_costs(database: Database, source: Path) -> OrderImportSummary:
    raw = _raw_rows(source)
    parsed = [_parse_line(row, line) for line, row in raw]
    unique: dict[tuple[str, str, str, str], OrderCostLine] = {}
    deduplicated = 0
    for line in parsed:
        existing = unique.get(line.key)
        if existing is None:
            unique[line.key] = line
        elif existing == line:
            deduplicated += 1
        else:
            raise BusinessInputError(
                "conflicting duplicate: "
                + "/".join(line.key)
            )

    inserted = 0
    updated = 0
    with database.transaction() as connection:
        for item in unique.values():
            exists = connection.execute(
                """
                SELECT 1 FROM order_cost_lines
                WHERE platform=? AND store=? AND order_id=? AND sku_id=?
                """,
                item.key,
            ).fetchone()
            connection.execute(
                """
                INSERT INTO order_cost_lines(
                    platform, store, order_id, sku_id, order_date,
                    quantity, unit_cost_cny, line_cost_cny, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, store, order_id, sku_id) DO UPDATE SET
                    order_date=excluded.order_date,
                    quantity=excluded.quantity,
                    unit_cost_cny=excluded.unit_cost_cny,
                    line_cost_cny=excluded.line_cost_cny,
                    source_file=excluded.source_file,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    item.platform, item.store, item.order_id, item.sku_id,
                    item.order_date.isoformat(), item.quantity,
                    str(item.unit_cost_cny), str(item.line_cost_cny),
                    source.name,
                ),
            )
            inserted += exists is None
            updated += exists is not None

    dates = [item.order_date for item in unique.values()]
    total = sum(
        (item.line_cost_cny for item in unique.values()),
        Decimal("0"),
    )
    return OrderImportSummary(
        read=len(raw),
        inserted=inserted,
        updated=updated,
        deduplicated=deduplicated,
        start=min(dates),
        end=max(dates),
        total_cost_cny=total.quantize(Decimal("0.01")),
    )
```

Reject an empty data file before opening the transaction.

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/analytics/test_order_costs.py -q
```

Expected: all order-cost tests PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/adwatch/analytics/order_costs.py \
  tests/analytics/test_order_costs.py
git commit -m "feat: import Chinese order cost workbooks"
```

### Task 3: Add store mapping, summaries, and CLI

**Files:**
- Modify: `src/adwatch/analytics/order_costs.py`
- Modify: `src/adwatch/cli.py`
- Modify: `tests/analytics/test_order_costs.py`
- Modify: `tests/test_business_cli.py`

- [ ] **Step 1: Write failing alias and summary tests**

Add repository tests:

```python
def test_map_store_requires_collected_target_and_summary_uses_source_store(
    tmp_path,
):
    source = tmp_path / "orders.csv"
    _write_csv(
        source,
        [[20260708, "shopee", "no4kud44da", "o", "1 bag", 1, 5]],
    )
    database = Database(tmp_path / "adwatch.sqlite3")
    database.migrate()
    import_order_costs(database, source)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO daily_ad_metrics(
                platform, store, account_id, campaign_id, sku_id, data_date,
                currency, spend, attributed_gmv, orders, roas, cpa, source
            ) VALUES (
                'shopee', '虾皮泰国', 'a', 'c', '__ALL__', '2026-07-08',
                'THB', '10', '100', 1, '10', '10', 'ziniao'
            )"""
        )
    map_store(database, "shopee", "no4kud44da", "虾皮泰国")
    rows = order_cost_summary(
        database, date(2026, 7, 8), date(2026, 7, 8)
    )
    assert rows[0].store == "no4kud44da"
    assert rows[0].canonical_store == "虾皮泰国"
```

Add CLI tests that assert:

```text
Imported order costs: read=9 inserted=9 updated=0 deduplicated=0
date_range=2026-07-08..2026-07-17 total_cost_cny=75.00
```

and JSON/markdown-free plain summary output with date, platform, source store,
canonical store, orders, units, and cost.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/analytics/test_order_costs.py tests/test_business_cli.py -q
```

Expected: FAIL because mapping, summary, and CLI commands do not exist.

- [ ] **Step 3: Implement mapping and summary**

Add:

```python
@dataclass(frozen=True)
class OrderCostSummary:
    order_date: date
    platform: str
    store: str
    canonical_store: str
    orders: int
    units: int
    total_cost_cny: Decimal


def map_store(
    database: Database,
    platform: str,
    source_store: str,
    canonical_store: str,
) -> None:
    normalized = platform.strip().lower()
    with database.transaction() as connection:
        target = connection.execute(
            """
            SELECT 1 FROM daily_ad_metrics
            WHERE platform=? AND store=? LIMIT 1
            """,
            (normalized, canonical_store.strip()),
        ).fetchone()
        if target is None:
            raise BusinessInputError(
                f"unknown collected store: {normalized}/{canonical_store}"
            )
        connection.execute(
            """
            INSERT INTO store_aliases(platform, source_store, canonical_store)
            VALUES (?, ?, ?)
            ON CONFLICT(platform, source_store) DO UPDATE SET
                canonical_store=excluded.canonical_store
            """,
            (normalized, source_store.strip(), canonical_store.strip()),
        )
```

Implement `order_cost_summary` with `COUNT(DISTINCT order_id)`,
`SUM(quantity)`, `SUM(CAST(line_cost_cny AS NUMERIC))`, and a left join to
`store_aliases`.

- [ ] **Step 4: Add CLI parsers and handlers**

Add parsers:

```python
import_orders = business_commands.add_parser("import-orders")
import_orders.add_argument("--file", type=Path, required=True)

map_store_command = business_commands.add_parser("map-store")
map_store_command.add_argument("--platform", required=True)
map_store_command.add_argument("--source", required=True)
map_store_command.add_argument("--target", required=True)

order_summary = business_commands.add_parser("order-summary")
order_summary.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
order_summary.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
```

Handlers must call the functions above, print the exact tested summaries, catch
`BusinessInputError`, and return exit code 2 without a traceback.

- [ ] **Step 5: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/analytics/test_order_costs.py tests/test_business_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/adwatch/analytics/order_costs.py src/adwatch/cli.py \
  tests/analytics/test_order_costs.py tests/test_business_cli.py
git commit -m "feat: map stores and summarize order costs"
```

### Task 4: Use CNY order cost without double conversion

**Files:**
- Modify: `src/adwatch/analytics/profit.py`
- Modify: `src/adwatch/storage/analytics.py`
- Modify: `src/adwatch/analytics/service.py`
- Modify: `tests/analytics/test_profit.py`
- Modify: `tests/analytics/test_service.py`

- [ ] **Step 1: Write failing profit test**

Add:

```python
def test_order_product_cost_cny_is_not_converted_again():
    result = calculate_profit(
        ProfitInput(
            gmv=Decimal("1000"),
            refunds=Decimal("0"),
            commission_rate=Decimal("0.10"),
            product_cost=Decimal("0"),
            ad_spend=Decimal("100"),
            seller_shipping=Decimal("0"),
            coupons=Decimal("0"),
            allocated_fixed_cost=Decimal("0"),
            exchange_rate_to_cny=Decimal("0.20"),
            product_cost_cny=Decimal("75"),
        )
    )
    assert result.net_sales_cny == Decimal("200.00")
    assert result.platform_commission_cny == Decimal("20.00")
    assert result.gross_profit_cny == Decimal("105.00")
    assert result.net_profit_cny == Decimal("85.00")
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/analytics/test_profit.py::test_order_product_cost_cny_is_not_converted_again \
  -q
```

Expected: FAIL because `ProfitInput` has no `product_cost_cny`.

- [ ] **Step 3: Refactor profit calculation to one CNY basis**

Add `product_cost_cny: Decimal | None = None` as the last dataclass field.
Calculate:

```python
rate = item.exchange_rate_to_cny
net_sales_cny = (item.gmv - item.refunds) * rate
commission_cny = (
    (item.gmv - item.refunds) * item.commission_rate * rate
)
resolved_product_cost_cny = (
    item.product_cost * rate
    if item.product_cost_cny is None
    else item.product_cost_cny
)
gross_profit_cny = (
    net_sales_cny - commission_cny - resolved_product_cost_cny
)
ad_spend_cny = item.ad_spend * rate
other_costs_cny = (
    item.seller_shipping + item.coupons + item.allocated_fixed_cost
) * rate
net_profit_cny = gross_profit_cny - ad_spend_cny - other_costs_cny
```

Compute break-even ROAS from CNY values:

```python
if ad_spend_cny != 0 and contribution_rate > 0:
    break_even_gmv_cny = (
        resolved_product_cost_cny + other_costs_cny + ad_spend_cny
    ) / contribution_rate
    break_even_roas = (
        break_even_gmv_cny / ad_spend_cny
    ).quantize(RATIO)
```

Keep prior behavior identical when `product_cost_cny is None`.

- [ ] **Step 4: Write failing safe-allocation service tests**

Create fixtures with:

1. one Shopee metric for `虾皮泰国/2026-07-08`, alias
   `no4kud44da -> 虾皮泰国`, and order cost 5 CNY;
2. two Shopee metrics for the same store/date and order cost 5 CNY.

Assert case 1 exposes `order_product_cost_cny == "5"` and produces one profit
result after other business inputs are seeded. Assert case 2 creates an open
`ambiguous_order_cost_allocation` alert and produces no profit result using that
order cost.

- [ ] **Step 5: Add safe order-cost aggregation to the analytics query**

Add CTEs:

```sql
WITH metric_counts AS (
    SELECT platform, store, data_date, COUNT(*) AS metric_count
    FROM daily_ad_metrics
    GROUP BY platform, store, data_date
),
daily_order_costs AS (
    SELECT
        line.platform,
        COALESCE(alias.canonical_store, line.store) AS canonical_store,
        line.order_date,
        SUM(CAST(line.line_cost_cny AS NUMERIC)) AS product_cost_cny
    FROM order_cost_lines AS line
    LEFT JOIN store_aliases AS alias
      ON alias.platform=line.platform
     AND alias.source_store=line.store
    GROUP BY line.platform, canonical_store, line.order_date
)
```

Join both CTEs to `metric`, and select:

```sql
CASE WHEN metric_counts.metric_count = 1
     THEN CAST(daily_order_costs.product_cost_cny AS TEXT)
END AS order_product_cost_cny,
CASE WHEN daily_order_costs.product_cost_cny IS NOT NULL
          AND metric_counts.metric_count > 1
     THEN 1 ELSE 0
END AS order_cost_allocation_ambiguous
```

- [ ] **Step 6: Wire the service**

Before the missing-input check, upsert
`ambiguous_order_cost_allocation` when the ambiguity flag is 1. Resolve the
product-cost requirement as:

```python
has_order_cost = row["order_product_cost_cny"] is not None
missing = [
    field for field in required
    if row[field] is None
    and not (field == "product_cost" and has_order_cost)
]
```

Pass:

```python
product_cost=Decimal(row["product_cost"] or "0"),
product_cost_cny=(
    None
    if row["order_product_cost_cny"] is None
    else Decimal(row["order_product_cost_cny"])
),
```

When allocation is ambiguous, keep the pending-data behavior and do not generate
a recommendation based on duplicated cost.

- [ ] **Step 7: Run analytics tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/analytics/test_profit.py tests/analytics/test_service.py -q
```

Expected: PASS, including all pre-existing profit behavior.

- [ ] **Step 8: Commit**

```bash
git add src/adwatch/analytics/profit.py src/adwatch/storage/analytics.py \
  src/adwatch/analytics/service.py tests/analytics/test_profit.py \
  tests/analytics/test_service.py
git commit -m "feat: apply CNY order costs safely"
```

### Task 5: Readiness, documentation, and regression verification

**Files:**
- Modify: `src/adwatch/cli.py`
- Modify: `tests/operations/test_launch_checklist.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing readiness test**

Insert one valid `order_cost_lines` row without `product_costs`, run the
launch-checklist path, and assert `business_costs` is absent.

- [ ] **Step 2: Update readiness query**

Replace the single-table predicate with:

```sql
SELECT (
    EXISTS(SELECT 1 FROM product_costs)
    OR EXISTS(SELECT 1 FROM order_cost_lines)
)
```

Convert the returned integer to `bool`.

- [ ] **Step 3: Document the workflow**

Add this exact workflow to README:

```bash
.venv/bin/adwatch business import-orders \
  --file /Users/yl/Desktop/订单SKU成本明细模板-shopee2.xlsx
.venv/bin/adwatch business map-store \
  --platform shopee --source no4kud44da --target 虾皮泰国
.venv/bin/adwatch business order-summary \
  --from 2026-07-08 --to 2026-07-17
```

Document that `数量` means sellable variation count, so one `5 bags` option has
quantity 1 and unit cost 17.

- [ ] **Step 4: Run the full verification suite**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check --select E,F,I src tests
.venv/bin/python -m compileall -q src tests
```

Expected: all tests PASS, ruff reports no errors, compileall exits 0.

- [ ] **Step 5: Commit**

```bash
git add README.md src/adwatch/cli.py \
  tests/operations/test_launch_checklist.py
git commit -m "docs: activate order cost launch workflow"
```

### Task 6: Import and verify the real Shopee workbook

**Files:**
- Read: `/Users/yl/Desktop/订单SKU成本明细模板-shopee2.xlsx`
- Modify runtime data only: `var/adwatch.sqlite3`

- [ ] **Step 1: Verify the safety switch**

Run:

```bash
rg -n '^ADWATCH_LIVE_WRITES=false$' .env
```

Expected: exactly one matching line.

- [ ] **Step 2: Back up SQLite**

Run:

```bash
.venv/bin/adwatch backup create \
  --output var/backups/pre-order-cost-import-2026-07-27.sqlite3
.venv/bin/adwatch backup verify \
  --path var/backups/pre-order-cost-import-2026-07-27.sqlite3
```

Expected: backup created and integrity check reports `ok`.

- [ ] **Step 3: Import the workbook**

Run:

```bash
.venv/bin/adwatch business import-orders \
  --file /Users/yl/Desktop/订单SKU成本明细模板-shopee2.xlsx
```

Expected:

```text
Imported order costs: read=9 inserted=9 updated=0 deduplicated=0
date_range=2026-07-08..2026-07-17 total_cost_cny=75.00
```

- [ ] **Step 4: Register the store alias**

Run:

```bash
.venv/bin/adwatch business map-store \
  --platform shopee --source no4kud44da --target 虾皮泰国
```

Expected: mapping confirmation with both store names.

- [ ] **Step 5: Verify summary and idempotency**

Run:

```bash
.venv/bin/adwatch business order-summary \
  --from 2026-07-08 --to 2026-07-17
.venv/bin/adwatch business import-orders \
  --file /Users/yl/Desktop/订单SKU成本明细模板-shopee2.xlsx
```

Expected: 9 orders, 9 variation units, 75.00 CNY; the second import reports
`inserted=0 updated=9`, and the summary remains 75.00.

- [ ] **Step 6: Refresh launch readiness**

Run:

```bash
.venv/bin/adwatch launch-checklist --format markdown
```

Expected: `business_costs` is no longer listed. No live write command is run.

- [ ] **Step 7: Commit implementation state**

Confirm `var/` and `.env` remain ignored:

```bash
git status --short
```

Expected: no runtime database, backup, report, or secret configuration is staged.

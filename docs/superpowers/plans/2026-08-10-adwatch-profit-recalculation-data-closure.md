# AdWatch 利润重算数据闭环 Implementation Plan

> **For agentic workers:** Execute this plan task-by-task in the current workspace. Preserve unrelated user changes and do not commit or push unless explicitly requested.

**Goal:** 修复退款 T+3 关账、混合履约订单成本覆盖和库存页 KPI 口径，使生产重算能生成可信利润或准确说明待补原因。

**Architecture:** 通过迁移 26 增加店铺默认履约历史和按平台/店铺/日期的每日退款事实；`RefundClosureService` 在范围重算前依据 T+3 与成功同步覆盖写入 `t3_closed_zero` 或 `pending_data`。订单同步按“快照 → SKU 策略 → 店铺默认”解析履约，货盘代发不等待发货即可冻结成本，自有备货继续走库存出库门禁。分析 lineage、库存查询和页面都读取分离后的事实口径。

**Tech Stack:** Python 3.11, SQLite, pytest, Ruff, stdlib HTML dashboard, Docker Compose/SSH deployment.

---

### Task 1: Add migration 29 for fulfillment defaults and daily refund facts

**Files:**
- Modify: `src/adwatch/storage/migrations.py` after the current migration 28
- Create: `tests/storage/test_profit_closure_migration.py`

- [x] **Step 1: Write the failing migration tests**

Add tests that migrate a fresh database and assert:

```python
def test_migration_26_creates_default_fulfillment_and_refund_tables(tmp_path):
    database = Database(tmp_path / "closure.sqlite3")
    database.migrate()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 29
        assert {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        } >= {"store_fulfillment_history", "daily_refund_inputs"}

def test_daily_refund_input_rejects_negative_amount(tmp_path):
    database = Database(tmp_path / "closure-check.sqlite3")
    database.migrate()
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO daily_refund_inputs(
                    platform, store, data_date, refund_amount_original,
                    currency, status, source_type, source_ref,
                    observed_on, closed_at, updated_at
                ) VALUES ('shopee','shop','2026-08-01','-1','THB',
                          'observed','test','','2026-08-10',NULL,
                          '2026-08-10T00:00:00Z')"""
            )
```

Use the existing migration test convention and also assert version 26 is idempotent and all tables/constraints survive reopening the database.

- [x] **Step 2: Run the migration tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/storage/test_profit_closure_migration.py -q
```

Expected: FAIL because migration 29 and its tables do not exist.

- [x] **Step 3: Implement the minimal migration**

Append migration 29 with:

```sql
CREATE TABLE store_fulfillment_history (
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('supplier_fulfilled', 'stocked')),
    supply_status TEXT NOT NULL CHECK(supply_status IN ('available', 'paused')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY(platform, store, effective_date)
);
CREATE INDEX store_fulfillment_effective_idx
ON store_fulfillment_history(platform, store, effective_date);

CREATE TABLE daily_refund_inputs (
    platform TEXT NOT NULL,
    store TEXT NOT NULL,
    data_date TEXT NOT NULL,
    refund_amount_original TEXT NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('observed', 't3_closed_zero', 'pending_data')
    ),
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    observed_on TEXT,
    closed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(platform, store, data_date),
    CHECK(CAST(refund_amount_original AS REAL) >= 0),
    CHECK(
        status IN ('observed', 't3_closed_zero')
        OR refund_amount_original != '0'
        OR source_type != 't3_no_refund_observed'
    )
);
CREATE INDEX daily_refund_inputs_date_idx
ON daily_refund_inputs(data_date, platform, store, status);
```

    The application-level service will enforce the stronger source/status rule; the database permits a zero placeholder for pending rows so existing import compatibility is preserved, while negative amounts are rejected by the database constraint.

- [x] **Step 4: Run the migration tests and verify GREEN**

Run the same command. Expected: all migration tests pass.

- [x] **Step 5: Run the existing migration regression set**

Run:

```bash
.venv/bin/python -m pytest tests/storage -q
```

Expected: all storage tests pass.

### Task 2: Implement T+3 refund closure service

**Files:**
- Create: `src/adwatch/analytics/refund_closure.py`
- Create: `tests/analytics/test_refund_closure.py`
- Modify: `src/adwatch/orders/repository.py` only if a narrow successful-run query helper is needed

- [x] **Step 1: Write failing service tests**

Cover one behavior per test:

```python
def test_closes_zero_after_t3_when_successful_sync_covers_window(tmp_path):
    database = seeded_database(tmp_path)
    insert_metric(database, "2026-08-01")
    insert_successful_sync(database, "2026-08-01", "2026-08-04")
    result = RefundClosureService(database, today=date(2026, 8, 5)).close(
        start=date(2026, 8, 1), end=date(2026, 8, 1)
    )
    assert result.closed_zero == 1
    assert refund_row(database)["status"] == "t3_closed_zero"

def test_does_not_close_before_t3_or_after_partial_sync(tmp_path):
    database = seeded_database(tmp_path)
    insert_metric(database, "2026-08-01")
    insert_partial_sync(database, "2026-08-01", "2026-08-04")
    result = RefundClosureService(database, today=date(2026, 8, 2)).close(
        start=date(2026, 8, 1), end=date(2026, 8, 1)
    )
    assert result.closed_zero == 0
    assert result.pending == 1

def test_refund_status_without_amount_stays_pending(tmp_path):
    database = seeded_database(tmp_path)
    insert_metric(database, "2026-08-01")
    insert_successful_sync(database, "2026-08-01", "2026-08-04")
    insert_refund_observation(database, order_date="2026-08-01", amount=None)
    result = RefundClosureService(database, today=date(2026, 8, 5)).close(
        start=date(2026, 8, 1), end=date(2026, 8, 1)
    )
    assert result.pending == 1
    assert refund_row(database)["status"] == "pending_data"

def test_closure_is_idempotent(tmp_path):
    database = seeded_database(tmp_path)
    # insert metric and successful sync as above
    service = RefundClosureService(database, today=date(2026, 8, 5))
    first = service.close(start=date(2026, 8, 1), end=date(2026, 8, 1))
    second = service.close(start=date(2026, 8, 1), end=date(2026, 8, 1))
    assert (first.closed_zero, second.closed_zero) == (1, 0)
```

Use real SQLite fixtures; do not mock the repository. Add a test for an existing `observed` amount remaining unchanged.

- [x] **Step 2: Run the service tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/analytics/test_refund_closure.py -q
```

Expected: FAIL because `RefundClosureService` and the daily fact repository do not exist.

- [x] **Step 3: Implement the service**

Create typed result rows with `closed_zero`, `pending`, `unchanged`, and per-date reasons. Use `today` injection for deterministic tests. Define T+3 eligibility as `data_date + timedelta(days=3) <= today`. Require a `order_sync_runs` row for the same platform/store with `status='success'`, `start_date <= data_date`, and `end_date >= data_date + 3 days`; treat `partial`, `failed`, and missing runs as pending.

For eligible dates, query `platform_refund_observations` joined to the date. If any status is non-empty and all amounts are NULL, upsert `pending_data` with source `platform_refund_observations` and detail reason returned by the result. If there are no refund rows/statuses, upsert amount `0`, status `t3_closed_zero`, source type `t3_no_refund_observed`. Do not update raw observation rows.

- [x] **Step 4: Run the service tests and verify GREEN**

Run the focused test command and expect all tests to pass.

### Task 3: Add default fulfillment resolution and correct order cost synchronization

**Files:**
- Modify: `src/adwatch/orders/fulfillment.py`
- Modify: `src/adwatch/orders/sync.py`
- Modify: `src/adwatch/inventory/service.py` only for a narrow order-cost status predicate if required
- Create or modify: `tests/orders/test_fulfillment.py`, `tests/orders/test_operations_sync.py`

- [x] **Step 1: Write failing fulfillment tests**

Add tests for default resolution and supplier cost before fulfillment:

```python
def test_store_default_resolves_when_sku_policy_is_missing(tmp_path):
    database = migrated_database(tmp_path)
    service = FulfillmentService(database)
    service.set_store_default(
        platform="shopee", store="shop", effective_date=date(2026, 7, 1),
        mode="supplier_fulfilled", supply_status="available"
    )
    snapshot = service.snapshot_order(
        platform="shopee", store="shop", order_id="ORDER-1",
        seller_sku="SKU-1", ordered_on=date(2026, 8, 1)
    )
    assert snapshot.mode == "supplier_fulfilled"
    assert snapshot.resolution_source == "store_default"

def test_supplier_fulfilled_costs_before_shipped_status(tmp_path):
    database = seeded_order_database(tmp_path, order_status="Order Received")
    insert_cost(database, seller_sku="SKU-1", effective_date="2026-07-01")
    insert_store_default(database)
    result = OperationsSyncService(database).sync()
    assert result.supplier_costed == 1
    assert count_cost_snapshots(database) == 1
```

Add a regression test showing a `stocked` order remains pending until inventory can be shipped, and a date-effective SKU override beats the store default.

- [x] **Step 2: Run the fulfillment tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/orders/test_fulfillment.py tests/orders/test_operations_sync.py -q
```

Expected: FAIL because there is no store-default table/service method and `OperationsSyncService` exits before supplier cost processing.

- [x] **Step 3: Implement minimal fulfillment resolution**

Extend `FulfillmentSnapshot` with `resolution_source`. Add `set_store_default()` and a default lookup using the latest effective date <= ordered date. In `snapshot_order()`, query SKU policy first, then store default; insert the source into `order_fulfillment_snapshots`. Keep existing snapshots immutable.

In `OperationsSyncService.sync()`, after cancellation and return handling, resolve cost and branch on `fulfillment.mode`: supplier fulfilled records order cost immediately; stocked then checks fulfillment status and calls `ship_order`. Preserve pending counters and idempotence.

- [x] **Step 4: Fix returned cost coverage in the analytics query**

Update `order_line_scope` in `src/adwatch/storage/analytics.py` to exclude rows whose latest refund status is in the returned set, using the same normalized statuses as `OperationsSyncService`. Ensure `snapshot_order_costs_raw` only compares expected retained-sale keys and continues to ignore `status='returned'` snapshots. Add a test with one returned and one retained line proving the date is complete for the retained line.

- [x] **Step 5: Run focused tests and verify GREEN**

Run the two order suites plus analytics cost tests:

```bash
.venv/bin/python -m pytest tests/orders tests/analytics/test_service.py -q
```

Expected: all focused tests pass.

### Task 4: Connect refund facts to analysis, lineage, and recalculation output

**Files:**
- Modify: `src/adwatch/storage/analytics.py`
- Modify: `src/adwatch/analytics/service.py`
- Modify: `src/adwatch/analytics/recalculation.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `tests/analytics/test_service.py`, `tests/analytics/test_recalculation.py`, `tests/dashboard/test_routes.py`

- [x] **Step 1: Write failing analysis tests**

Add tests proving a T+3 zero produces a profit result when all other inputs are ready and that a returned/refund pending row blocks profit with the correct lineage detail:

```python
def test_analysis_uses_t3_closed_zero_refund_fact(tmp_path):
    database = ready_analysis_database(tmp_path)
    insert_daily_refund(database, "2026-08-01", "0", "t3_closed_zero")
    summary = AnalysisService(database).run(date(2026, 8, 1))
    assert summary.profit_results == 1
    assert lineage(database, "refunds")["detail_code"] == "t3_no_refund_observed"

def test_analysis_keeps_refund_pending_when_return_amount_missing(tmp_path):
    database = ready_analysis_database(tmp_path)
    insert_daily_refund(database, "2026-08-01", "0", "pending_data")
    summary = AnalysisService(database).run(date(2026, 8, 1))
    assert summary.profit_results == 0
    assert lineage(database, "refunds")["detail_code"] == "refund_source_missing"
```

Extend recalculation tests to assert `missing_inputs['refunds']` decreases for T+3-closed dates and the report retains the date-level reason.

- [x] **Step 2: Run the analysis tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/analytics/test_service.py tests/analytics/test_recalculation.py -q
```

Expected: FAIL because the repository still reads `product_costs.refund_amount` only.

- [x] **Step 3: Implement daily refund precedence and closure invocation**

Join `daily_refund_inputs` by platform/store/data date in `AnalyticsRepository.load_analysis_rows()`. Resolve refund amount/status/source columns with daily fact precedence; retain product-cost fallback only when the legacy row is explicitly `observed`. Change `_profit_input_lineage()` to emit source type `daily_refund_inputs`, source date/data date, and `t3_no_refund_observed` for `t3_closed_zero`.

Call `RefundClosureService.close(start, end)` at the beginning of `AnalysisRecalculationService.run()` inside the same transaction boundary used by the recalculation. Recompute missing input counts after closure and preserve atomic failure behavior.

- [x] **Step 4: Update route message labels**

Change `RECALCULATION_INPUT_LABELS` so `product_cost` is displayed as `订单成本覆盖` when the lineage detail is `order_cost_coverage_incomplete`, while `product_costs` fallback remains `商品单价`. Keep `refunds` as `退款金额`, and add the T+3 reason to the success notice.

- [x] **Step 5: Run focused analysis/dashboard tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/analytics tests/dashboard/test_routes.py -q
```

Expected: all focused tests pass.

### Task 5: Correct inventory read model and page KPIs

**Files:**
- Modify: `src/adwatch/inventory/service.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `src/adwatch/dashboard/app.py` only if new view data must be passed
- Modify: `tests/inventory/test_service.py`, `tests/dashboard/test_views.py`, `tests/dashboard/test_app.py`

- [x] **Step 1: Write failing KPI tests**

Add a fixture with platform SKU mappings and platform inventory snapshots but no manual balances. Assert the page reports platform SKU count and does not render the old zero as the total:

```python
def test_inventory_page_uses_platform_sku_count_when_manual_ledger_empty(tmp_path):
    database = seeded_inventory_database(tmp_path)
    html = render_inventory_page(database, date(2026, 8, 3), csrf_token="token")
    assert "平台 SKU 数" in html
    assert "324" in html
    assert "手工台账 SKU" in html
```

Add service tests for `platform_sku_count()`, `latest_platform_inventory()`, and `list_order_cost_gaps()` returning separate records from `list_missing_sku_costs()`.

- [x] **Step 2: Run the view/service tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/inventory/test_service.py tests/dashboard/test_views.py tests/dashboard/test_app.py -q
```

Expected: FAIL because the current cards use `inventory_balances` and the page has no platform KPI query.

- [x] **Step 3: Implement the read model and labels**

Add read-only queries that use the latest `platform_sku_mappings`/`platform_inventory_snapshots` facts and order cost snapshots. Keep manual balances for the ledger table and label them separately. Update the four cards and empty states without changing write routes or CSRF behavior.

- [x] **Step 4: Run the dashboard tests and verify GREEN**

Run the focused command again. Expected: all tests pass and no existing form assertions regress.

### Task 6: Full verification, package audit, deployment, and production acceptance

**Files/Artifacts:**
- Modify only repair-related source/tests/docs from Tasks 1–5
- Create: `VERSION` update and deployment report under `var/reports/` if the existing deployment workflow requires it
- Do not include: any `.env` file, temporary database, cookies, or session exports

- [ ] **Step 1: Run local quality gates**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: pytest and Ruff exit 0, compileall exits 0, and diff check prints no errors. Record exact counts in `progress.md`.

- [ ] **Step 2: Run a local end-to-end recalculation fixture**

Use a temporary SQLite database to migrate, insert one successful sync covering T+3, insert metrics and ready cost/fee/rate inputs, run `AnalysisRecalculationService`, and assert one profit result plus a `t3_no_refund_observed` lineage row. Run a second time and assert unchanged differences.

- [ ] **Step 3: Audit the deployment package**

Create a temporary archive using an explicit file list or `tar --exclude='.env'`; list its contents and fail the step if any path basename equals `.env` or contains cookies/session exports. Preserve the archive only until upload succeeds.

- [ ] **Step 4: Back up and deploy the authorized server**

Follow `服务器自动化部署` instructions: verify SSH target and project directory, create a dated SQLite backup and SHA256, upload the source archive, apply migrations, rebuild the `adwatch` image/container, and keep the previous image for rollback. Do not delete unrelated server files.

- [ ] **Step 5: Verify production behavior**

Run health checks, inspect recent logs for errors, call the inventory page and recalc endpoint, run the authorized order sync/recalculation range, and query the database for:

```sql
PRAGMA integrity_check;
SELECT status, COUNT(*) FROM daily_refund_inputs GROUP BY status;
SELECT COUNT(*) FROM profit_results;
```

Capture the exact deployment ID/image digest, backup hash, recalc summary, and page status in `progress.md`. If any check fails, stop and use the preserved image/database backup for a reversible rollback.

## Self-review checklist

- [x] Every design requirement maps to Tasks 1–6.
- [x] T+3, partial-sync, missing refund amount, default fulfillment, SKU override, returned cost, separate KPI, tests, package exclusion, backup, and production checks are explicit.
- [x] No unresolved business decision remains; every step has a concrete file, test, command, and expected result.
- [x] All names used by later tasks (`store_fulfillment_history`, `daily_refund_inputs`, `RefundClosureService`, `t3_closed_zero`, `t3_no_refund_observed`) are defined before use.

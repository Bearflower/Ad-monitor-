# SKU Fulfillment Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add effective-dated SKU fulfillment modes so supplier-fulfilled orders create COGS without inventory while stocked SKUs retain inventory accounting.

**Architecture:** Store effective-dated fulfillment policies per platform/store/Seller SKU and freeze the resolved policy on each order line. Route order synchronization through separate supplier-fulfilled and stocked paths, keeping cost snapshots common and inventory movements exclusive to stocked SKUs.

**Tech Stack:** Python 3.11+, SQLite, pytest, standard-library HTTP server.

---

### Task 1: Fulfillment persistence and service

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `src/adwatch/orders/fulfillment.py`
- Test: `tests/storage/test_fulfillment_migration.py`
- Test: `tests/orders/test_fulfillment.py`

- [ ] Write a migration test proving both fulfillment tables, primary keys and allowed values exist.
- [ ] Run `pytest tests/storage/test_fulfillment_migration.py -q` and verify it fails because the tables do not exist.
- [ ] Add the next numbered migration with `sku_fulfillment_history` and `order_fulfillment_snapshots`.
- [ ] Write service tests proving effective-date resolution, immutable order snapshots and idempotent bulk supplier marking.
- [ ] Run `pytest tests/orders/test_fulfillment.py -q` and verify the missing service failure.
- [ ] Implement `FulfillmentService.set_policy`, `resolve_for_order`, `snapshot_order` and `mark_current_skus_supplier_fulfilled`.
- [ ] Run both test files and verify they pass.
- [ ] Commit with `feat: add effective dated sku fulfillment`.

### Task 2: Split supplier and stocked order processing

**Files:**
- Modify: `src/adwatch/inventory/service.py`
- Modify: `src/adwatch/orders/sync.py`
- Modify: `tests/orders/test_operations_sync.py`

- [ ] Write failing tests proving supplier-fulfilled orders create confirmed cost snapshots without inventory, cancelled orders create neither, stocked orders still require inventory, and missing policies return `pending_fulfillment`.
- [ ] Run `pytest tests/orders/test_operations_sync.py -q` and verify the supplier path fails.
- [ ] Add an idempotent `InventoryService.record_order_cost` method that does not create inventory movements.
- [ ] Resolve and freeze fulfillment before processing each order; route supplier orders to cost-only and stocked orders to `ship_order`.
- [ ] Add `pending_fulfillment` and `supplier_costed` to `OperationsSyncResult`.
- [ ] Run order and inventory tests and verify they pass.
- [ ] Commit with `feat: support supplier fulfilled order costs`.

### Task 3: CLI and Web maintenance

**Files:**
- Modify: `src/adwatch/cli.py`
- Modify: `src/adwatch/dashboard/routes.py`
- Modify: `src/adwatch/dashboard/views.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/dashboard/test_routes.py`
- Modify: `tests/dashboard/test_views.py`

- [ ] Write failing CLI tests for single-SKU policy setting and bulk marking.
- [ ] Write failing Web tests for fulfillment mode, effective date and supply status validation.
- [ ] Add `business set-fulfillment` and `business mark-current-skus-supplier-fulfilled`.
- [ ] Add the SKU fulfillment Web form and route through `FulfillmentService`.
- [ ] Run CLI and dashboard tests and verify they pass.
- [ ] Commit with `feat: add sku fulfillment controls`.

### Task 4: Analytics semantics, migration and verification

**Files:**
- Modify: `src/adwatch/storage/analytics.py`
- Modify: `src/adwatch/analytics/service.py`
- Modify: `README.md`
- Test: `tests/analytics/test_service.py`
- Test: `tests/test_cli_end_to_end.py`

- [ ] Write failing analytics tests proving supplier fulfillment does not require inventory fields and stocked fulfillment retains inventory gates.
- [ ] Extend analysis rows with the resolved fulfillment mode and set supplier inventory capability to `not_applicable`.
- [ ] Add an end-to-end test for supplier SKU policy → order cost snapshot → profit input with zero inventory movements.
- [ ] Document the hybrid SKU-only fulfillment workflow and transition commands.
- [ ] Run `pytest -q`, `ruff check src tests`, and `git diff --check`.
- [ ] Run the bulk marker and order sync against the production database after backing it up.
- [ ] Verify 65 supplier SKUs, zero cost gaps, zero pending inventory for supplier orders, and zero inventory movements.
- [ ] Commit with `feat: complete hybrid sku fulfillment workflow`.

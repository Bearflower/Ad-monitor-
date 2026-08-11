# AdWatch 有效订单行与订单成本复用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除旧占位订单行与真实同步行的重复数量，只对去重后真实缺口复用订单日有效的 SKU 成本冻结订单快照。

**Architecture:** 迁移 30/31 新增并升级只读 `effective_platform_order_lines` view。它以规范店铺的业务键选择真实 Item/Model 行，保留原始表，并标记多个真实行的待核冲突。订单同步、利润覆盖、库存缺口、经营读模型和分润统一使用该 view；既有 `OperationsSyncService` 继续负责库存/履约约束下的幂等成本冻结。

**Tech Stack:** Python 3.11、SQLite window functions、pytest、Ruff、Docker Compose。

---

### Task 1: 创建有效订单行 SQLite view

**Files:**
- Modify: `src/adwatch/storage/migrations.py`
- Create: `tests/storage/test_effective_order_lines_migration.py`

- [x] **Step 1: 写失败迁移测试**

```python
def test_effective_order_lines_prefers_resolved_row_over_legacy_placeholder(tmp_path):
    database = Database(tmp_path / "effective-lines.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        connection.executemany(
            """INSERT INTO platform_order_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                legacy_line("SKU-1", "SKU-1", 1, "2026-07-01T00:00:00Z"),
                resolved_line("item-1", "model-1", 1, "2026-07-02T00:00:00Z"),
            ),
        )
    row = database.connect().execute(
        "SELECT item_id, model_id, quantity, resolution_status "
        "FROM effective_platform_order_lines"
    ).fetchone()
    assert tuple(row) == ("item-1", "model-1", 1, "ready")
```

- [x] **Step 2: 确认测试因 view 不存在而失败**

Run: `.venv/bin/python -m pytest tests/storage/test_effective_order_lines_migration.py -q`

Expected: `OperationalError: no such table: effective_platform_order_lines`.

- [x] **Step 3: 在迁移 30 创建 view**

```sql
CREATE VIEW effective_platform_order_lines AS
WITH ranked AS (
    SELECT pol.*,
           SUM(CASE WHEN pol.item_id=pol.seller_sku
                         AND pol.model_id=pol.seller_sku
                    THEN 0 ELSE 1 END)
               OVER (PARTITION BY pol.platform, pol.store,
                                  pol.order_id, pol.seller_sku)
               AS resolved_line_count,
           ROW_NUMBER() OVER (
               PARTITION BY pol.platform, pol.store,
                            pol.order_id, pol.seller_sku
               ORDER BY CASE WHEN pol.item_id=pol.seller_sku
                                   AND pol.model_id=pol.seller_sku
                             THEN 1 ELSE 0 END,
                        pol.source_updated_at DESC,
                        pol.item_id, pol.model_id
           ) AS source_rank
    FROM platform_order_lines AS pol
)
SELECT ranked.*,
       CASE WHEN resolved_line_count > 1
            THEN 'ambiguous_resolved' ELSE 'ready' END AS resolution_status
FROM ranked WHERE source_rank=1;
```

- [x] **Step 4: 确认迁移测试通过，并覆盖多真实行冲突**

Run: `.venv/bin/python -m pytest tests/storage/test_effective_order_lines_migration.py -q`

Expected: PASS，且多条真实 Item/Model 同一业务键返回 `ambiguous_resolved`。

### Task 2: 订单同步只处理有效业务订单行

**Files:**
- Modify: `src/adwatch/orders/sync.py`
- Modify: `tests/orders/test_operations_sync.py`

- [x] **Step 1: 写重复订单行回归测试**

```python
def test_sync_uses_resolved_order_line_once_when_legacy_placeholder_exists(tmp_path):
    database = Database(tmp_path / "dedup-sync.sqlite3")
    database.migrate()
    _set_store_default_supplier(database)
    _set_cost(database, "SKU-1", date(2026, 7, 1), Decimal("5"))
    repository = OrderRepository(database)
    repository.upsert_orders((legacy_order("ORDER-1", "SKU-1"),))
    repository.upsert_orders((resolved_order("ORDER-1", "SKU-1"),))

    result = OperationsSyncService(database).sync()

    assert result.supplier_costed == 1
    snapshot = _snapshot(database, "ORDER-1")
    assert tuple(snapshot) == (1, "5", "5")
```

- [x] **Step 2: 确认旧 `SUM(quantity)` 行为使测试失败**

Run: `.venv/bin/python -m pytest tests/orders/test_operations_sync.py::test_sync_uses_resolved_order_line_once_when_legacy_placeholder_exists -q`

Expected: FAIL，旧实现会创建数量 2 的快照或与现有快照产生错误覆盖。

- [x] **Step 3: 改用 view 并跳过待核冲突**

```sql
SELECT platform, store, order_id, seller_sku, quantity,
       substr(ordered_at, 1, 10) AS ordered_on,
       substr(source_updated_at, 1, 10) AS event_date,
       order_status, logistics_status, refund_status
FROM effective_platform_order_lines
WHERE resolution_status='ready'
ORDER BY ordered_on, order_id, seller_sku
```

`OperationsSyncResult` 增加 `pending_order_identity`；若 view 返回
`ambiguous_resolved`，不写成本或库存，计入该字段。

- [x] **Step 4: 确认重复同步与真实行冲突测试通过**

Run: `.venv/bin/python -m pytest tests/orders/test_operations_sync.py -q`

Expected: PASS；第二次同步不增加快照，多真实行只计待核。

### Task 3: 利润与库存查询采用相同有效订单行口径

**Files:**
- Modify: `src/adwatch/storage/analytics.py`
- Modify: `src/adwatch/inventory/service.py`
- Modify: `tests/analytics/test_service.py`
- Modify: `tests/inventory/test_service.py`

- [x] **Step 1: 写跨消费者一致性失败测试**

```python
def test_duplicate_legacy_and_resolved_line_is_not_an_order_cost_gap(tmp_path):
    database = seeded_duplicate_order_database(tmp_path)
    _insert_confirmed_snapshot(database, quantity=1, unit_cost=Decimal("5"))

    assert InventoryService(database).list_order_cost_gaps() == ()
    row = AnalyticsRepository(database).load_analysis_rows(date(2026, 7, 23))[0]
    assert row["order_cost_coverage_incomplete"] == 0
```

- [x] **Step 2: 确认库存和分析当前仍将数量相加而失败**

Run: `.venv/bin/python -m pytest tests/inventory/test_service.py tests/analytics/test_service.py -q`

Expected: 新测试 FAIL，旧 SQL 从 `platform_order_lines` 聚合出数量 2。

- [x] **Step 3: 将订单范围 CTE 和缺口查询改读 view**

```sql
FROM effective_platform_order_lines AS line
WHERE line.resolution_status='ready'
```

保留取消/退货过滤、店铺别名匹配和确认快照数量判断；对
`ambiguous_resolved` 不生成自动成本覆盖结论。

- [x] **Step 4: 确认一致性回归通过**

Run: `.venv/bin/python -m pytest tests/inventory/test_service.py tests/analytics/test_service.py -q`

Expected: PASS；同一订单在同步、库存页和利润分析均只计一次。

### Task 4: CLI、质量门禁与生产回填

**Files:**
- Modify: `src/adwatch/cli.py`
- Modify: `tests/test_business_cli.py`
- Modify: `task_plan.md`
- Modify: `progress.md`
- Modify: `findings.md`

- [x] **Step 1: 写 CLI 结果的失败测试**

```python
def test_business_sync_orders_reports_pending_order_identity(tmp_path, capsys):
    exit_code = main(["business", "sync-orders"])
    assert exit_code == 2
    assert "pending_order_identity=1" in capsys.readouterr().out
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_business_cli.py -q`

Expected: FAIL，当前输出没有 `pending_order_identity`。

- [x] **Step 3: 输出待核身份计数并将其视为非零退出条件**

```python
f"pending_order_identity={result.pending_order_identity} "
```

将 `result.pending_order_identity` 加入 CLI 的 exit-code 2 条件；日报已有同步结果输出时同步加入该字段。

- [x] **Step 4: 执行本地质量门禁**

Run: `.venv/bin/ruff check src tests && .venv/bin/python -m pytest -q && .venv/bin/python -m compileall -q src tests && git diff --check`

Expected: 全部通过。

- [x] **Step 5: 审查、幻觉扫描和生产部署**

独立审查已完成并修复全部 Critical/Important：迁移 31 按规范店铺再去重；别名等价的履约/成本/库存移动快照保持幂等；订单成本覆盖逐订单行校验；日报将待核订单身份归为 partial。生产已备份 SQLite、重建健康容器并验收；8 条/14 件唯一且成本版本完全匹配的历史半截快照已安全补齐，订单成本缺口归零。

Run: `rg -n "TBD|TODO|implement later|fill in|Similar to|appropriate error|handle edge" docs/superpowers/specs/2026-08-10-adwatch-effective-order-lines-cost-backfill-design.md docs/superpowers/plans/2026-08-10-adwatch-effective-order-lines-cost-backfill.md`

Expected: 无结果。

在独立审查修复所有 Critical/Important 后，创建不含 `.env` 的源码包，先备份生产 SQLite，
重建 `adwatch` 容器，再执行：

```bash
docker exec adwatch adwatch business sync-orders
docker exec adwatch adwatch analyze --from 2026-07-21 --to 2026-08-03 \
  --output /app/var/reports/effective-order-lines-recalc.json
```

验收：SQLite `integrity_check=ok`；有效订单行去重后成本缺口、同步结果和利润覆盖一致；
`/adwatch/` 与 `/adwatch/inventory` 返回 HTTP 200。
